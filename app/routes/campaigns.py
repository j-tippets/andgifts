from types import SimpleNamespace

from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user

from app.extensions import db, limiter
from app.models import Campaign, CampaignRecipe, CampaignRule, SuggestedAction, ActionLog, Contact, User
from app.models.campaigns import _timing_label as timing_label_phrase
from app.models.timeline import CustomEventType
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str
from app.services import suggestion_engine
from app.services import campaign_rules
from app.services import llm

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")


def _can_manage(campaign):
    """Org admins can manage any personal flow in their org (their own or
    any agent's); an agent can only manage their own."""
    if campaign.org_id != current_user.org_id:
        return False
    if current_user.is_admin:
        return True
    return campaign.owner_user_id == current_user.id


def _has_pending_actions(campaign):
    """True if this flow still has pending suggestions sitting on
    someone's dashboard."""
    return db.session.query(
        SuggestedAction.query.filter_by(source_campaign_id=campaign.id, status="pending").exists()
    ).scalar()


def _resulting_actions(campaign, limit=25):
    """Actions that resulted from this specific flow -- the completed/sent
    record (ActionLog), joined back through the SuggestedAction that
    generated it, most recent first."""
    return (
        ActionLog.query
        .join(SuggestedAction, ActionLog.suggested_action_id == SuggestedAction.id)
        .filter(SuggestedAction.source_campaign_id == campaign.id)
        .order_by(ActionLog.sent_at.desc())
        .limit(limit)
        .all()
    )


def _can_manage_recipe(recipe):
    """Only a local (agency) recipe can be managed here, and only by an
    admin in that same org. Global recipes are platform_admin-only, over
    in /app-admin."""
    return (
        current_user.is_admin
        and recipe.org_id is not None
        and recipe.org_id == current_user.org_id
    )


def _org_event_type_choices():
    """(key, label) pairs for the local Flow Library's trigger dropdown:
    this org's shared (org-scope) milestones -- including whatever came
    from the org's PracticeType preset, now ordinary rows like anything
    else. Personal milestones are deliberately excluded -- a local
    recipe is a shared team template any agent can copy, so it can't
    rely on a milestone that's private to whichever admin happened to
    author it. Sorted case-insensitively in Python rather than via SQL
    ORDER BY -- most DB collations sort uppercase before lowercase, so
    a mixed-case label like "showing" would otherwise land after every
    capitalized one instead of where it alphabetically belongs."""
    org_types = CustomEventType.query.filter_by(org_id=current_user.org_id, scope="org").all()
    org_types.sort(key=lambda t: t.label.lower())
    return [(t.key, t.label) for t in org_types]


def _personal_event_type_choices():
    """(key, label) pairs for a personal flow's trigger dropdown: this
    org's shared milestones plus this agent's own personal milestones.
    Same case-insensitive sort as _org_event_type_choices above."""
    query = CustomEventType.query.filter_by(org_id=current_user.org_id)
    visible = CustomEventType.visible_to(query, current_user).all()
    visible.sort(key=lambda t: t.label.lower())
    return [(t.key, t.label) for t in visible]


def _condition_form_kwargs(org):
    """Shared dropdown data for the generic condition builder (used by
    both the personal-flow wizard and the local Flow Library forms)."""
    fields = campaign_rules.condition_field_choices(org)
    operator_map = {
        key: [
            (op, campaign_rules.OPERATOR_LABELS.get(op, op))
            for op in campaign_rules.operators_for_field(key, org)
        ]
        for key, _label, _value_type, _options in fields
    }
    return dict(condition_fields=fields, condition_operator_map=operator_map)


def _recipe_form_kwargs():
    """Shared dropdown data for the local-recipe new/edit forms."""
    return dict(
        event_types=_org_event_type_choices(),
        gift_items=current_user.org.available_catalog_items(),
        **_condition_form_kwargs(current_user.org),
    )


def _save_recipe_from_form(recipe):
    recipe.name = request.form["name"].strip()
    recipe.description = request.form.get("description", "").strip() or None
    _timing_from_form(recipe)

    recipe.action_type = request.form["action_type"]
    gift_id = request.form.get("suggested_gift_id", "").strip()
    recipe.suggested_gift_id = gift_id or None
    recipe.price_max_cents = dollars_to_cents(request.form.get("price_max"))
    recipe.use_llm_gift_selection = bool(request.form.get("use_llm_gift_selection"))
    recipe.add_note = bool(request.form.get("add_note"))
    recipe.note_text = request.form.get("note_text", "").strip() or None

    recipe.use_llm_copy = bool(request.form.get("use_llm_copy"))
    recipe.message_template = request.form.get("message_template", "").strip() or None
    recipe.llm_prompt_hint = request.form.get("llm_prompt_hint", "").strip() or None

    recipe.rules = _conditions_from_form(CampaignRecipeRule, current_user.org)


def _build_flow_spec_from_form(default_name="Untitled preview"):
    """A plain in-memory stand-in for a Campaign/CampaignRecipe, built
    straight from submitted (not-yet-saved) form data -- lets 'Preview'
    dry-run a flow's matching logic before anything is written to the
    database."""
    try:
        timing_amount = max(0, int(request.form.get("timing_amount", "1")))
    except ValueError:
        timing_amount = 1

    try:
        recur_interval_amount = max(1, int(request.form.get("recur_interval_amount", "1")))
    except ValueError:
        recur_interval_amount = 1

    max_occurrences_raw = request.form.get("max_occurrences", "").strip()
    try:
        max_occurrences = max(1, int(max_occurrences_raw)) if max_occurrences_raw else None
    except ValueError:
        max_occurrences = None

    return SimpleNamespace(
        name=request.form.get("name", "").strip() or default_name,
        event_type=request.form.get("event_type"),
        timing_direction=request.form.get("timing_direction", "after"),
        timing_amount=timing_amount,
        timing_unit=request.form.get("timing_unit", "day"),
        repeat_enabled=bool(request.form.get("repeat_enabled")),
        recur_interval_amount=recur_interval_amount,
        recur_interval_unit=request.form.get("recur_interval_unit", "year"),
        max_occurrences=max_occurrences,
        rules=_conditions_from_form(CampaignRule, current_user.org),
        price_max_cents=dollars_to_cents(request.form.get("price_max")),
        use_llm_gift_selection=bool(request.form.get("use_llm_gift_selection")),
        add_note=bool(request.form.get("add_note")),
        note_text=request.form.get("note_text", "").strip() or None,
        action_type=request.form.get("action_type"),
        suggested_gift_id=request.form.get("suggested_gift_id", "").strip() or None,
        use_llm_copy=bool(request.form.get("use_llm_copy")),
        message_template=request.form.get("message_template", "").strip() or None,
        llm_prompt_hint=request.form.get("llm_prompt_hint", "").strip() or None,
    )


def _describe_condition(rule, field_labels):
    """One condition row as a plain-English clause -- deterministic from
    structured fields, not LLM-written (same principle as
    Campaign.timing_label()). A bit literal for some built-in fields
    today (e.g. the cooldown field's own label already reads like a
    sentence); fine to hand-tune per-field phrasing later without
    touching the condition model itself."""
    label = field_labels.get(rule.field, rule.field)
    operator = (rule.config or {}).get("operator")
    value = (rule.config or {}).get("value")
    operator_label = campaign_rules.OPERATOR_LABELS.get(operator, operator or "")
    if operator in ("is_empty", "is_not_empty"):
        return f"{label} {operator_label}"
    return f"{label} {operator_label} {value}"


def _event_type_label(event_type, org):
    """Plain display label for an event_type key -- a CustomEventType's
    own label when it's one of this org's (shared or personal) custom
    milestones, else a generic title-cased fallback (covers the
    'custom' one-off sentinel and any stale/legacy key)."""
    custom = CustomEventType.query.filter_by(org_id=org.id, key=event_type).first()
    return custom.label if custom else (event_type or "").replace("_", " ").title()


def _describe_flow_sentence(spec, org):
    """Deterministic plain-English summary of a flow's full
    configuration, for the wizard's Review step -- built from
    structured fields the same way Campaign.timing_label() is, not
    generated by an LLM. &Gifts never sends anything without an agent's
    explicit approval on the Today tab, so unlike the general Flows
    spec this never needs an 'ask for approval' clause -- that's not a
    per-flow setting here, it's always true."""
    timing_phrase_text = timing_label_phrase(spec.timing_direction, spec.timing_amount, spec.timing_unit)
    event_label_text = _event_type_label(spec.event_type, org)

    if spec.action_type == "gift":
        if spec.use_llm_gift_selection:
            action = "send a gift"
            if spec.price_max_cents:
                action += f" (LLM-picked, up to ${spec.price_max_cents / 100:.0f})"
            else:
                action += " (LLM-picked)"
        elif spec.suggested_gift_id:
            gift = next((g for g in current_user.org.available_catalog_items() if g.id == spec.suggested_gift_id), None)
            action = f"send {gift.name}" if gift else "send the selected gift"
        else:
            action = "send a gift (none selected yet)"
        if spec.add_note:
            action += ' with a note ("' + spec.note_text.strip() + '")' if spec.note_text else " with a note (LLM-written)"
    else:
        kind_label = {"email": "an email", "text": "a text", "handwritten_note": "a handwritten note"}[spec.action_type]
        action = f"send {kind_label}"
        if spec.use_llm_copy:
            action += ", written by the LLM"

    field_labels = {key: label for key, label, _value_type, _options in campaign_rules.condition_field_choices(org)}
    condition_phrases = [_describe_condition(rule, field_labels) for rule in spec.rules]
    condition_clause = f", if {' and '.join(condition_phrases)}," if condition_phrases else ","

    sentence = f"{timing_phrase_text.capitalize()} {event_label_text.lower()}{condition_clause} {action}."
    if spec.repeat_enabled:
        interval_amount = getattr(spec, "recur_interval_amount", None) or 1
        interval_unit = getattr(spec, "recur_interval_unit", None) or "year"
        unit_word = interval_unit + ("s" if interval_amount != 1 else "")
        sentence += f" Repeat this every {interval_amount} {unit_word}."
        max_occurrences = getattr(spec, "max_occurrences", None)
        if max_occurrences:
            sentence += f" Run this a maximum of {max_occurrences} time{'s' if max_occurrences != 1 else ''}."
    else:
        sentence += " This only ever fires once per contact."

    return sentence


def _review_stats(preview_results):
    """Small, honest set of numbers for the Review step -- deliberately
    scoped to the same 14-day lookahead window preview_flow_matches
    already checks, rather than projecting a full year of activity
    (which would need scanning every contact's event regardless of the
    window, a bigger computation this MVP doesn't attempt yet)."""
    matching_contacts = len(preview_results)
    total_spend_cents = sum(r["gift_price_cents"] or 0 for r in preview_results if r.get("gift_price_cents"))
    next_trigger = min((r["trigger_date"] for r in preview_results), default=None)
    return dict(
        matching_contacts=matching_contacts,
        total_spend_cents=total_spend_cents,
        next_trigger=next_trigger,
    )


def _run_preview(spec, contacts_query):
    """Dry-run a flow spec (real or not-yet-saved) against a set of
    contacts -- shared by the wizard's Review step and both Flow
    Library forms' Preview button."""
    contacts = contacts_query.filter(Contact.do_not_contact.is_(False)).all()
    return suggestion_engine.preview_flow_matches(spec, contacts, current_user.org, limit=15)


def _my_contacts_query():
    """Contacts visible to the current user -- used to preview a
    personal flow against their own book."""
    return Contact.visible_to(Contact.query.filter_by(org_id=current_user.org_id), current_user)


def _org_contacts_query():
    """Every contact in the org -- used to preview a library flow,
    which isn't tied to one agent yet."""
    return Contact.query.filter_by(org_id=current_user.org_id)


def _wizard_rules(campaign):
    """Condition rows for the wizard's Who step to display. On a POST
    re-render (preview, or the name-required bounce-back) this rebuilds
    from what was just submitted, so in-progress edits to conditions
    survive the round trip the same way the rest of the form now does
    (see the request.form fallbacks in wizard.html) instead of reverting
    to whatever's saved -- or to nothing, for a brand new flow."""
    if request.method == "POST":
        return _conditions_from_form(CampaignRule, current_user.org)
    return campaign.rules if campaign else []


def _campaign_form_kwargs(scope="personal"):
    """Shared dropdown data for the campaign wizard. scope='library'
    (only reachable for admins, only from campaign_new) swaps in the
    org-only event types _org_event_type_choices() already used by the
    Flow Library forms -- a library flow is a shared team template, so
    it can't offer a milestone private to whichever admin happened to
    build it. campaign_edit never passes scope; a personal Campaign
    being edited is always 'personal'."""
    return dict(
        event_types=_org_event_type_choices() if scope == "library" else _personal_event_type_choices(),
        gift_items=current_user.org.available_catalog_items(),
        **_condition_form_kwargs(current_user.org),
    )


def _conditions_from_form(rule_cls, org):
    """Build a list of CampaignRule/CampaignRecipeRule instances (not yet
    attached to any parent) from the generic condition builder's
    parallel form arrays: condition_field[], condition_operator[],
    condition_value[]. Anything that doesn't validate against
    campaign_rules.operators_for_field is dropped rather than trusted
    blindly -- the client-side dropdowns already constrain this, but a
    condition row referencing a deleted custom field, or a field/operator
    pairing that doesn't make sense, shouldn't silently get saved. Rows
    left with a value-requiring operator but a blank value are dropped
    too -- the wizard's JS already prunes these before submit, but this
    is the backstop for any path that reaches this function without it
    (a non-JS submit, or a future caller)."""
    fields = request.form.getlist("condition_field")
    operators = request.form.getlist("condition_operator")
    values = request.form.getlist("condition_value")

    rows = []
    for position, (field, operator, value) in enumerate(zip(fields, operators, values)):
        field = field.strip()
        operator = operator.strip()
        value = value.strip()
        if not field or not operator:
            continue
        if operator not in campaign_rules.operators_for_field(field, org):
            continue
        if not value and operator not in campaign_rules.VALUE_LESS_OPERATORS:
            continue
        rows.append(rule_cls(field=field, config={"operator": operator, "value": value}, position=position))
    return rows


def _timing_from_form(target):
    """Reads the Trigger step's fields onto target (a Campaign,
    CampaignRecipe, or the SimpleNamespace preview spec) in place."""
    target.event_type = request.form.get("event_type")
    target.timing_direction = request.form.get("timing_direction", "after")
    try:
        target.timing_amount = max(0, int(request.form.get("timing_amount", "1")))
    except ValueError:
        target.timing_amount = 1
    target.timing_unit = request.form.get("timing_unit", "day")
    target.repeat_enabled = bool(request.form.get("repeat_enabled"))
    try:
        target.recur_interval_amount = max(1, int(request.form.get("recur_interval_amount", "1")))
    except ValueError:
        target.recur_interval_amount = 1
    target.recur_interval_unit = request.form.get("recur_interval_unit", "year")

    max_occurrences_raw = request.form.get("max_occurrences", "").strip()
    if max_occurrences_raw:
        try:
            target.max_occurrences = max(1, int(max_occurrences_raw))
        except ValueError:
            target.max_occurrences = None
    else:
        target.max_occurrences = None


def _save_campaign_from_form(campaign):
    campaign.name = request.form["name"].strip()
    campaign.description = request.form.get("description", "").strip() or None
    _timing_from_form(campaign)

    campaign.action_type = request.form["action_type"]
    gift_id = request.form.get("suggested_gift_id", "").strip()
    campaign.suggested_gift_id = gift_id or None
    campaign.price_max_cents = dollars_to_cents(request.form.get("price_max"))
    campaign.use_llm_gift_selection = bool(request.form.get("use_llm_gift_selection"))
    campaign.add_note = bool(request.form.get("add_note"))
    campaign.note_text = request.form.get("note_text", "").strip() or None

    campaign.use_llm_copy = bool(request.form.get("use_llm_copy"))
    campaign.message_template = request.form.get("message_template", "").strip() or None
    campaign.llm_prompt_hint = request.form.get("llm_prompt_hint", "").strip() or None

    # Who step -- rebuild the whole condition set from what was
    # submitted rather than patching individual rows. Simpler and safer
    # than trying to diff old vs. new: cascade="all, delete-orphan" on
    # the relationship cleans up whatever isn't in the new list.
    campaign.rules = _conditions_from_form(CampaignRule, current_user.org)


@campaigns_bp.route("/")
@login_required
def list_campaigns():
    my_campaigns = (
        Campaign.query.filter_by(org_id=current_user.org_id, owner_user_id=current_user.id)
        .order_by(Campaign.name)
        .all()
    )
    return render_template("campaigns/list.html", my_campaigns=my_campaigns)


@campaigns_bp.route("/actions")
@login_required
def actions_report():
    """Report of actions: everything still pending (Upcoming) and
    everything already logged as sent/approved (Recently completed).

    Agency admins see every action across the org, with an optional
    filter down to one agent. A single agent only ever sees their own:
    actions from their own flows, or (for the older non-flow path)
    actions on contacts privately owned by them or shared org-wide."""
    org_id = current_user.org_id
    selected_agent = request.args.get("agent", "").strip()

    upcoming_query = (
        SuggestedAction.query
        .filter_by(org_id=org_id, status="pending")
        .outerjoin(Campaign, SuggestedAction.source_campaign_id == Campaign.id)
        .join(Contact, SuggestedAction.contact_id == Contact.id)
    )
    completed_query = (
        ActionLog.query
        .filter_by(org_id=org_id)
        .outerjoin(SuggestedAction, ActionLog.suggested_action_id == SuggestedAction.id)
        .outerjoin(Campaign, SuggestedAction.source_campaign_id == Campaign.id)
        .join(Contact, ActionLog.contact_id == Contact.id)
    )

    if not current_user.is_admin:
        upcoming_query = upcoming_query.filter(db.or_(
            Campaign.owner_user_id == current_user.id,
            db.and_(
                SuggestedAction.source_campaign_id.is_(None),
                db.or_(Contact.owner_user_id == current_user.id, Contact.owner_user_id.is_(None)),
            ),
        ))
        completed_query = completed_query.filter(db.or_(
            ActionLog.approved_by_user_id == current_user.id,
            Campaign.owner_user_id == current_user.id,
            db.and_(
                SuggestedAction.source_campaign_id.is_(None),
                db.or_(Contact.owner_user_id == current_user.id, Contact.owner_user_id.is_(None)),
            ),
        ))
    elif selected_agent == "unassigned":
        upcoming_query = upcoming_query.filter(
            SuggestedAction.source_campaign_id.is_(None), Contact.owner_user_id.is_(None)
        )
        completed_query = completed_query.filter(ActionLog.approved_by_user_id.is_(None))
    elif selected_agent:
        upcoming_query = upcoming_query.filter(db.or_(
            Campaign.owner_user_id == selected_agent,
            db.and_(SuggestedAction.source_campaign_id.is_(None), Contact.owner_user_id == selected_agent),
        ))
        completed_query = completed_query.filter(ActionLog.approved_by_user_id == selected_agent)

    upcoming = upcoming_query.order_by(SuggestedAction.target_date).all()
    recently_completed = completed_query.order_by(ActionLog.sent_at.desc()).limit(50).all()

    agents = None
    if current_user.is_admin:
        agents = (
            User.query.filter_by(org_id=org_id, status="active")
            .order_by(User.first_name, User.last_name)
            .all()
        )

    return render_template(
        "campaigns/actions.html",
        upcoming=upcoming,
        recently_completed=recently_completed,
        agents=agents,
        selected_agent=selected_agent,
    )


@campaigns_bp.route("/book")
@login_required
def recipe_book():
    """The Flow Library: every global (platform-authored) flow, plus
    this org's own local flows."""
    recipes = (
        CampaignRecipe.query.filter(
            CampaignRecipe.is_active.is_(True),
            db.or_(CampaignRecipe.org_id.is_(None), CampaignRecipe.org_id == current_user.org_id),
        )
        .order_by(CampaignRecipe.name)
        .all()
    )
    return render_template("campaigns/book.html", recipes=recipes)


@campaigns_bp.route("/book/<recipe_id>/add", methods=["POST"])
@login_required
def add_from_recipe(recipe_id):
    """Copy a flow (global or this org's own local one) into the
    current user's own personal Campaign. Every live flow belongs to
    one agent -- there's no more agency-wide scope here; an agency
    admin who wants something for the whole team authors it as a local
    flow in the Flow Library instead, and each agent (including the
    admin) adds their own copy from there."""
    recipe = CampaignRecipe.query.filter(
        CampaignRecipe.id == recipe_id,
        CampaignRecipe.is_active.is_(True),
        db.or_(CampaignRecipe.org_id.is_(None), CampaignRecipe.org_id == current_user.org_id),
    ).first_or_404()

    campaign = Campaign.from_recipe(
        recipe,
        org_id=current_user.org_id,
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
    )
    db.session.add(campaign)
    db.session.commit()

    flash(f"Added \u201c{campaign.name}\u201d to your flows.", "success")
    return redirect(url_for("campaigns.list_campaigns"))


@campaigns_bp.route("/library/<recipe_id>/edit", methods=["GET", "POST"])
@login_required
def library_edit(recipe_id):
    recipe = CampaignRecipe.query.get_or_404(recipe_id)
    if not _can_manage_recipe(recipe):
        flash("You don't have permission to edit that flow.", "error")
        return redirect(url_for("campaigns.recipe_book"))

    if request.method == "GET":
        return render_template(
            "campaigns/library_edit.html",
            recipe=recipe,
            price_max_display=cents_to_dollars_str(recipe.price_max_cents),
            **_recipe_form_kwargs(),
        )

    if not request.form.get("name", "").strip() or not request.form.get("event_type"):
        flash("Name and a trigger event are required.", "error")
        return redirect(url_for("campaigns.library_edit", recipe_id=recipe.id))

    if request.form.get("action") == "preview":
        spec = _build_flow_spec_from_form(default_name=recipe.name)
        preview_results = _run_preview(spec, _org_contacts_query())
        return render_template(
            "campaigns/library_edit.html",
            recipe=recipe,
            price_max_display=cents_to_dollars_str(recipe.price_max_cents),
            preview_results=preview_results,
            preview_scope_label="every contact in your agency",
            previewed_spec=spec,
            flow_sentence=_describe_flow_sentence(spec, current_user.org),
            review_stats=_review_stats(preview_results),
            **_recipe_form_kwargs(),
        )

    _save_recipe_from_form(recipe)
    db.session.commit()
    flash(f"Updated \u201c{recipe.name}\u201d.", "success")
    return redirect(url_for("campaigns.recipe_book"))


@campaigns_bp.route("/library/<recipe_id>/toggle-active", methods=["POST"])
@login_required
def library_toggle_active(recipe_id):
    recipe = CampaignRecipe.query.get_or_404(recipe_id)
    if not _can_manage_recipe(recipe):
        flash("You don't have permission to change that flow.", "error")
        return redirect(url_for("campaigns.recipe_book"))

    recipe.is_active = not recipe.is_active
    db.session.commit()
    flash(f"\u201c{recipe.name}\u201d is now {'active' if recipe.is_active else 'inactive'}.", "success")
    return redirect(url_for("campaigns.recipe_book"))


@campaigns_bp.route("/library/<recipe_id>/delete", methods=["POST"])
@login_required
def library_delete(recipe_id):
    """Hard delete -- safe by design, since every Campaign already
    copied from this recipe has its own independent copy of the fields
    (Campaign.from_recipe) and just loses the 'copied from' breadcrumb."""
    recipe = CampaignRecipe.query.get_or_404(recipe_id)
    if not _can_manage_recipe(recipe):
        flash("You don't have permission to delete that flow.", "error")
        return redirect(url_for("campaigns.recipe_book"))

    name = recipe.name
    db.session.delete(recipe)
    db.session.commit()
    flash(f"Deleted \u201c{name}\u201d from your agency's Flow Library.", "success")
    return redirect(url_for("campaigns.recipe_book"))


@campaigns_bp.route("/<campaign_id>/toggle-active", methods=["POST"])
@login_required
def toggle_active(campaign_id):
    campaign = Campaign.query.filter_by(id=campaign_id, org_id=current_user.org_id).first_or_404()
    if not _can_manage(campaign):
        flash("You don't have permission to change that campaign.", "error")
        return redirect(url_for("campaigns.list_campaigns"))

    campaign.is_active = not campaign.is_active
    db.session.commit()
    flash(f"\u201c{campaign.name}\u201d is now {'active' if campaign.is_active else 'paused'}.", "success")
    # Pause/Resume is submitted from both the flows list (list.html's
    # kebab menu) and a single flow's own detail page -- a flag rather
    # than a raw next-URL, so there's no open-redirect surface to worry
    # about validating.
    if request.form.get("next") == "detail":
        return redirect(url_for("campaigns.campaign_detail", campaign_id=campaign.id))
    return redirect(url_for("campaigns.list_campaigns"))


@campaigns_bp.route("/new", methods=["GET", "POST"])
@login_required
def campaign_new():
    """Build a flow from scratch -- a personal Campaign by default, or
    (admin only, reached via ?scope=library from the Flow Library's
    '+ Build from scratch' button) a CampaignRecipe added straight to
    the agency's Flow Library instead. This one wizard now covers both;
    there used to be a second, separate flat-form route (library_new)
    for the library case, but its fields were already identical to
    this wizard's (_save_recipe_from_form and _save_campaign_from_form
    read the exact same request.form keys), so scope is now just a
    fork in this route rather than a whole parallel UI to keep in
    sync. scope rides along as a hidden form field (see wizard.html)
    so it survives the preview round trip and the final save."""
    scope = request.values.get("scope", "personal")
    if scope not in ("personal", "library"):
        scope = "personal"
    if scope == "library" and not current_user.is_admin:
        flash("Only an agency admin can add a flow to the library.", "error")
        return redirect(url_for("campaigns.recipe_book"))

    if request.method == "GET":
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            rules=_wizard_rules(None),
            scope=scope,
            **_campaign_form_kwargs(scope),
        )

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            rules=_wizard_rules(None),
            scope=scope,
            **_campaign_form_kwargs(scope),
        )

    if not request.form.get("action_type", "").strip():
        flash("Choose what this flow should do.", "error")
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            rules=_wizard_rules(None),
            scope=scope,
            **_campaign_form_kwargs(scope),
        )

    if request.form.get("action") == "preview":
        spec = _build_flow_spec_from_form()
        if scope == "library":
            preview_results = _run_preview(spec, _org_contacts_query())
            preview_scope_label = "every contact in your agency"
        else:
            preview_results = _run_preview(spec, _my_contacts_query())
            preview_scope_label = "your own contacts"
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            rules=_wizard_rules(None),
            spec=spec,
            previewed_spec=spec,
            preview_results=preview_results,
            preview_scope_label=preview_scope_label,
            flow_sentence=_describe_flow_sentence(spec, current_user.org),
            review_stats=_review_stats(preview_results),
            scope=scope,
            **_campaign_form_kwargs(scope),
        )

    if scope == "library":
        recipe = CampaignRecipe(is_active=True, org_id=current_user.org_id)
        _save_recipe_from_form(recipe)
        db.session.add(recipe)
        db.session.commit()
        flash(f"Added \u201c{recipe.name}\u201d to your agency's Flow Library.", "success")
        return redirect(url_for("campaigns.recipe_book"))

    campaign = Campaign(
        org_id=current_user.org_id,
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        is_active=True,
    )
    _save_campaign_from_form(campaign)
    db.session.add(campaign)
    db.session.commit()

    flash(f"Created \u201c{campaign.name}\u201d.", "success")
    return redirect(url_for("campaigns.list_campaigns"))


@campaigns_bp.route("/<campaign_id>")
@login_required
def campaign_detail(campaign_id):
    """The landing page for one flow -- its plain-English summary, the
    actions it's produced, and pause/delete -- separate from the editor
    itself (campaign_edit) so opening a flow to check on it doesn't
    also dump you into a 4-step wizard. 'Edit flow' is the one link
    into campaign_edit from here."""
    campaign = Campaign.query.filter_by(id=campaign_id, org_id=current_user.org_id).first_or_404()
    if not _can_manage(campaign):
        flash("You don't have permission to view that flow.", "error")
        return redirect(url_for("campaigns.list_campaigns"))

    return render_template(
        "campaigns/detail.html",
        campaign=campaign,
        flow_sentence=_describe_flow_sentence(campaign, current_user.org),
        can_delete=_can_manage(campaign) and not _has_pending_actions(campaign),
        resulting_actions=_resulting_actions(campaign),
    )


@campaigns_bp.route("/<campaign_id>/edit", methods=["GET", "POST"])
@login_required
def campaign_edit(campaign_id):
    campaign = Campaign.query.filter_by(id=campaign_id, org_id=current_user.org_id).first_or_404()
    if not _can_manage(campaign):
        flash("You don't have permission to edit that flow.", "error")
        return redirect(url_for("campaigns.list_campaigns"))

    if request.method == "GET":
        return render_template(
            "campaigns/wizard.html",
            campaign=campaign,
            rules=_wizard_rules(campaign),
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            **_campaign_form_kwargs(),
        )

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return render_template(
            "campaigns/wizard.html",
            campaign=campaign,
            rules=_wizard_rules(campaign),
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            **_campaign_form_kwargs(),
        )

    if not request.form.get("action_type", "").strip():
        flash("Choose what this flow should do.", "error")
        return render_template(
            "campaigns/wizard.html",
            campaign=campaign,
            rules=_wizard_rules(campaign),
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            **_campaign_form_kwargs(),
        )

    if request.form.get("action") == "preview":
        spec = _build_flow_spec_from_form(default_name=campaign.name)
        owner = campaign.owner or current_user
        contacts_query = Contact.visible_to(Contact.query.filter_by(org_id=current_user.org_id), owner)
        scope_label = "your own contacts" if owner.id == current_user.id else f"{owner.full_name}'s contacts"
        preview_results = _run_preview(spec, contacts_query)
        return render_template(
            "campaigns/wizard.html",
            campaign=campaign,
            rules=_wizard_rules(campaign),
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            preview_results=preview_results,
            preview_scope_label=scope_label,
            previewed_spec=spec,
            flow_sentence=_describe_flow_sentence(spec, current_user.org),
            review_stats=_review_stats(preview_results),
            **_campaign_form_kwargs(),
        )

    _save_campaign_from_form(campaign)
    db.session.commit()
    flash(f"Updated \u201c{campaign.name}\u201d.", "success")
    return redirect(url_for("campaigns.campaign_detail", campaign_id=campaign.id))


@campaigns_bp.route("/preview-message", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def preview_message():
    """AJAX endpoint backing the 'Example LLM output' box in the
    wizard's message dialog (email/text/handwritten_note). Runs the
    same generate_message() used for real sends, against a made-up
    contact/event so an agent can see roughly what the LLM will write
    from their hint before saving anything -- no contact matching, no
    Campaign/CampaignRecipe involved. Rate-limited since (unlike the
    rest of the wizard) this fires a real Anthropic API call on
    demand rather than only during scheduled suggestion generation."""
    prompt_hint = request.form.get("llm_prompt_hint", "").strip()
    event_type = request.form.get("event_type", "").strip()

    event_label = _event_type_label(event_type, current_user.org) if event_type else "milestone"
    fake_contact = SimpleNamespace(household_name="Jordan & Casey Smith")
    fake_event = SimpleNamespace(display_label=lambda: event_label)

    message = llm.generate_message(prompt_hint or None, fake_contact, fake_event)
    return jsonify(message=message)


@campaigns_bp.route("/<campaign_id>/delete", methods=["POST"])
@login_required
def campaign_delete(campaign_id):
    campaign = Campaign.query.filter_by(id=campaign_id, org_id=current_user.org_id).first_or_404()
    if not _can_manage(campaign):
        flash("You don't have permission to delete that flow.", "error")
        return redirect(url_for("campaigns.list_campaigns"))

    # May still have live pending suggestions on someone's dashboard;
    # make them resolve those first rather than silently orphaning a
    # card mid-flight.
    if _has_pending_actions(campaign):
        flash(
            f"\u201c{campaign.name}\u201d still has pending suggestions waiting for approval. "
            "Resolve (approve or skip) those first, then delete it.",
            "error",
        )
        return redirect(url_for("campaigns.list_campaigns"))

    name = campaign.name
    db.session.delete(campaign)
    db.session.commit()
    flash(f"Deleted \u201c{name}\u201d.", "success")
    return redirect(url_for("campaigns.list_campaigns"))
