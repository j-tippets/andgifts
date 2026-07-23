from types import SimpleNamespace

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Campaign, CampaignRecipe, CampaignRule, SuggestedAction, ActionLog, Contact, User
from app.models.campaigns import _timing_label as timing_label_phrase
from app.models.timeline import STANDARD_EVENT_TYPES, CustomEventType
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str
from app.services import suggestion_engine
from app.services import campaign_rules

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
    built-ins plus this org's shared (org-scope) milestones. Personal
    milestones are deliberately excluded -- a local recipe is a shared
    team template any agent can copy, so it can't rely on a milestone
    that's private to whichever admin happened to author it."""
    standard = [(t, t.replace("_", " ").title()) for t in STANDARD_EVENT_TYPES if t != "custom"]
    org_types = (
        CustomEventType.query.filter_by(org_id=current_user.org_id, scope="org")
        .order_by(CustomEventType.label).all()
    )
    return standard + [(t.key, t.label) for t in org_types]


def _personal_event_type_choices():
    """(key, label) pairs for a personal flow's trigger dropdown:
    built-ins, this org's shared milestones, and this agent's own
    personal milestones."""
    standard = [(t, t.replace("_", " ").title()) for t in STANDARD_EVENT_TYPES if t != "custom"]
    query = CustomEventType.query.filter_by(org_id=current_user.org_id)
    visible = CustomEventType.visible_to(query, current_user).order_by(CustomEventType.label).all()
    return standard + [(t.key, t.label) for t in visible]


def _condition_form_kwargs(org):
    """Shared dropdown data for the generic condition builder (used by
    both the personal-flow wizard and the local Flow Library forms)."""
    fields = campaign_rules.condition_field_choices(org)
    operator_map = {
        key: [
            (op, campaign_rules.OPERATOR_LABELS.get(op, op))
            for op in campaign_rules.operators_for_field(key, org)
        ]
        for key, _label, _value_type in fields
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

    return SimpleNamespace(
        name=request.form.get("name", "").strip() or default_name,
        event_type=request.form.get("event_type"),
        timing_direction=request.form.get("timing_direction", "after"),
        timing_amount=timing_amount,
        timing_unit=request.form.get("timing_unit", "day"),
        repeat_enabled=bool(request.form.get("repeat_enabled")),
        rules=_conditions_from_form(CampaignRule, current_user.org),
        price_max_cents=dollars_to_cents(request.form.get("price_max")),
        use_llm_gift_selection=bool(request.form.get("use_llm_gift_selection")),
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


def _describe_flow_sentence(spec, org):
    """Deterministic plain-English summary of a flow's full
    configuration, for the wizard's Review step -- built from
    structured fields the same way Campaign.timing_label() is, not
    generated by an LLM. &Gifts never sends anything without an agent's
    explicit approval on the Today tab, so unlike the general Flows
    spec this never needs an 'ask for approval' clause -- that's not a
    per-flow setting here, it's always true."""
    timing_phrase_text = timing_label_phrase(spec.timing_direction, spec.timing_amount, spec.timing_unit)

    if spec.action_type == "gift":
        if spec.use_llm_gift_selection:
            action = "recommend the best gift"
            if spec.price_max_cents:
                action += f" under ${spec.price_max_cents / 100:.0f}"
            action += " based on the client's interests"
        elif spec.suggested_gift_id:
            gift = next((g for g in current_user.org.available_catalog_items() if g.id == spec.suggested_gift_id), None)
            action = f"send {gift.name}" if gift else "send the selected gift"
        else:
            action = "send a gift (none selected yet)"
    else:
        kind_label = {"email": "an email", "text": "a text", "handwritten_note": "a handwritten note"}[spec.action_type]
        action = f"send {kind_label}"
        if spec.use_llm_copy:
            action += ", written by the LLM"

    sentence = f"{timing_phrase_text.capitalize()}, {action}."
    if not spec.repeat_enabled:
        sentence += " This only ever fires once per contact."

    field_labels = {key: label for key, label, _value_type in campaign_rules.condition_field_choices(org)}
    condition_phrases = [_describe_condition(rule, field_labels) for rule in spec.rules]
    if condition_phrases:
        sentence += " Skip anyone who doesn't match: " + "; ".join(condition_phrases) + "."

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


def _campaign_form_kwargs():
    """Shared dropdown data for the campaign wizard."""
    return dict(
        event_types=_personal_event_type_choices(),
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
    pairing that doesn't make sense, shouldn't silently get saved."""
    fields = request.form.getlist("condition_field")
    operators = request.form.getlist("condition_operator")
    values = request.form.getlist("condition_value")

    rows = []
    for position, (field, operator, value) in enumerate(zip(fields, operators, values)):
        field = field.strip()
        operator = operator.strip()
        if not field or not operator:
            continue
        if operator not in campaign_rules.operators_for_field(field, org):
            continue
        rows.append(rule_cls(field=field, config={"operator": operator, "value": value.strip()}, position=position))
    return rows


def _timing_from_form(target):
    """Reads the Event/Timing steps' fields onto target (a Campaign,
    CampaignRecipe, or the SimpleNamespace preview spec) in place."""
    target.event_type = request.form.get("event_type")
    target.timing_direction = request.form.get("timing_direction", "after")
    try:
        target.timing_amount = max(0, int(request.form.get("timing_amount", "1")))
    except ValueError:
        target.timing_amount = 1
    target.timing_unit = request.form.get("timing_unit", "day")
    target.repeat_enabled = bool(request.form.get("repeat_enabled"))


def _save_campaign_from_form(campaign):
    campaign.name = request.form["name"].strip()
    campaign.description = request.form.get("description", "").strip() or None
    _timing_from_form(campaign)

    campaign.action_type = request.form["action_type"]
    gift_id = request.form.get("suggested_gift_id", "").strip()
    campaign.suggested_gift_id = gift_id or None
    campaign.price_max_cents = dollars_to_cents(request.form.get("price_max"))
    campaign.use_llm_gift_selection = bool(request.form.get("use_llm_gift_selection"))

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


@campaigns_bp.route("/library/new", methods=["GET", "POST"])
@login_required
def library_new():
    """Agency admins author local flows here -- shown only in this
    org's Flow Library, alongside the platform's global flows. Each
    agent (including the admin) still has to add it to get it running
    for their own contacts."""
    if not current_user.is_admin:
        flash("Only an agency admin can add a flow to the library.", "error")
        return redirect(url_for("campaigns.recipe_book"))

    if request.method == "GET":
        return render_template("campaigns/library_new.html", **_recipe_form_kwargs())

    if not request.form.get("name", "").strip() or not request.form.get("event_type"):
        flash("Name and a trigger event are required.", "error")
        return render_template("campaigns/library_new.html", **_recipe_form_kwargs())

    if request.form.get("action") == "preview":
        spec = _build_flow_spec_from_form()
        preview_results = _run_preview(spec, _org_contacts_query())
        return render_template(
            "campaigns/library_new.html",
            spec=spec,
            preview_results=preview_results,
            preview_scope_label="every contact in your agency",
            flow_sentence=_describe_flow_sentence(spec, current_user.org),
            review_stats=_review_stats(preview_results),
            **_recipe_form_kwargs(),
        )

    recipe = CampaignRecipe(is_active=True, org_id=current_user.org_id)
    _save_recipe_from_form(recipe)
    db.session.add(recipe)
    db.session.commit()
    flash(f"Added \u201c{recipe.name}\u201d to your agency's Flow Library.", "success")
    return redirect(url_for("campaigns.recipe_book"))


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
    return redirect(url_for("campaigns.list_campaigns"))


@campaigns_bp.route("/new", methods=["GET", "POST"])
@login_required
def campaign_new():
    """Build a flow from scratch. Always a personal Campaign, scoped to
    the builder's own contacts -- there's no 'add to the agency Flow
    Library instead' choice here anymore. Publishing a reusable
    template for the whole team is a separate, deliberate action
    (library_new, admin-only), not a fork-in-the-road inside this
    wizard."""
    if request.method == "GET":
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            **_campaign_form_kwargs(),
        )

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            **_campaign_form_kwargs(),
        )

    if request.form.get("action") == "preview":
        spec = _build_flow_spec_from_form()
        preview_results = _run_preview(spec, _my_contacts_query())
        return render_template(
            "campaigns/wizard.html",
            campaign=None,
            spec=spec,
            previewed_spec=spec,
            preview_results=preview_results,
            preview_scope_label="your own contacts",
            flow_sentence=_describe_flow_sentence(spec, current_user.org),
            review_stats=_review_stats(preview_results),
            **_campaign_form_kwargs(),
        )

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
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            can_delete=_can_manage(campaign) and not _has_pending_actions(campaign),
            resulting_actions=_resulting_actions(campaign),
            **_campaign_form_kwargs(),
        )

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return redirect(url_for("campaigns.campaign_edit", campaign_id=campaign.id))

    if request.form.get("action") == "preview":
        spec = _build_flow_spec_from_form(default_name=campaign.name)
        owner = campaign.owner or current_user
        contacts_query = Contact.visible_to(Contact.query.filter_by(org_id=current_user.org_id), owner)
        scope_label = "your own contacts" if owner.id == current_user.id else f"{owner.full_name}'s contacts"
        preview_results = _run_preview(spec, contacts_query)
        return render_template(
            "campaigns/wizard.html",
            campaign=campaign,
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            can_delete=_can_manage(campaign) and not _has_pending_actions(campaign),
            preview_results=preview_results,
            preview_scope_label=scope_label,
            previewed_spec=spec,
            flow_sentence=_describe_flow_sentence(spec, current_user.org),
            review_stats=_review_stats(preview_results),
            resulting_actions=_resulting_actions(campaign),
            **_campaign_form_kwargs(),
        )

    _save_campaign_from_form(campaign)
    db.session.commit()
    flash(f"Updated \u201c{campaign.name}\u201d.", "success")
    return redirect(url_for("campaigns.list_campaigns"))


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
