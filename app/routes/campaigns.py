from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Campaign, CampaignRecipe, User, SuggestedAction
from app.models.timeline import STANDARD_EVENT_TYPES
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str

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


def _can_manage_recipe(recipe):
    """Only a local (agency) recipe can be managed here, and only by an
    admin in that same org. Global recipes are platform_admin-only, over
    in /app-admin."""
    return (
        current_user.is_admin
        and recipe.org_id is not None
        and recipe.org_id == current_user.org_id
    )


def _recipe_form_kwargs():
    """Shared dropdown data for the local-recipe new/edit forms."""
    return dict(
        event_types=STANDARD_EVENT_TYPES,
        gift_items=current_user.org.available_catalog_items(),
    )


def _save_recipe_from_form(recipe):
    recipe.name = request.form["name"].strip()
    recipe.description = request.form.get("description", "").strip() or None
    recipe.event_type = request.form["event_type"]

    try:
        recipe.offset_days = int(request.form.get("offset_days", "0"))
    except ValueError:
        recipe.offset_days = 0

    recipe.interest_tag = request.form.get("interest_tag", "").strip() or None
    recipe.price_max_cents = dollars_to_cents(request.form.get("price_max"))
    recipe.use_llm_gift_selection = bool(request.form.get("use_llm_gift_selection"))

    recipe.action_type = request.form["action_type"]
    gift_id = request.form.get("suggested_gift_id", "").strip()
    recipe.suggested_gift_id = gift_id or None

    recipe.use_llm_copy = bool(request.form.get("use_llm_copy"))
    recipe.message_template = request.form.get("message_template", "").strip() or None
    recipe.llm_prompt_hint = request.form.get("llm_prompt_hint", "").strip() or None


def _campaign_form_kwargs():
    """Shared dropdown data for the campaign new/edit forms."""
    return dict(
        event_types=STANDARD_EVENT_TYPES,
        gift_items=current_user.org.available_catalog_items(),
    )


def _save_campaign_from_form(campaign):
    campaign.name = request.form["name"].strip()
    campaign.description = request.form.get("description", "").strip() or None
    campaign.event_type = request.form["event_type"]

    try:
        campaign.offset_days = int(request.form.get("offset_days", "0"))
    except ValueError:
        campaign.offset_days = 0

    campaign.interest_tag = request.form.get("interest_tag", "").strip() or None
    campaign.price_max_cents = dollars_to_cents(request.form.get("price_max"))
    campaign.use_llm_gift_selection = bool(request.form.get("use_llm_gift_selection"))

    campaign.action_type = request.form["action_type"]
    gift_id = request.form.get("suggested_gift_id", "").strip()
    campaign.suggested_gift_id = gift_id or None

    campaign.use_llm_copy = bool(request.form.get("use_llm_copy"))
    campaign.message_template = request.form.get("message_template", "").strip() or None
    campaign.llm_prompt_hint = request.form.get("llm_prompt_hint", "").strip() or None


@campaigns_bp.route("/")
@login_required
def list_campaigns():
    org_id = current_user.org_id

    my_campaigns = (
        Campaign.query.filter_by(org_id=org_id, owner_user_id=current_user.id)
        .order_by(Campaign.name)
        .all()
    )

    other_personal_by_agent = []
    if current_user.is_admin:
        others = (
            Campaign.query.filter(
                Campaign.org_id == org_id,
                Campaign.owner_user_id.isnot(None),
                Campaign.owner_user_id != current_user.id,
            )
            .order_by(Campaign.name)
            .all()
        )
        by_owner = {}
        for c in others:
            by_owner.setdefault(c.owner_user_id, []).append(c)
        for owner_id, campaigns in by_owner.items():
            owner = User.query.get(owner_id)
            other_personal_by_agent.append((owner, campaigns))
        other_personal_by_agent.sort(key=lambda pair: pair[0].full_name if pair[0] else "")

    return render_template(
        "campaigns/list.html",
        my_campaigns=my_campaigns,
        other_personal_by_agent=other_personal_by_agent,
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

    flash(f"Added \u201c{campaign.name}\u201d to your campaigns.", "success")
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
    """Build a personal flow from scratch, just for your own contacts.
    Want something for the whole team instead? Add it to your agency's
    Flow Library (as a local flow) so every agent -- including you --
    can add their own copy from there."""
    if request.method == "GET":
        return render_template("campaigns/new.html", **_campaign_form_kwargs())

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return render_template("campaigns/new.html", **_campaign_form_kwargs())

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
            "campaigns/edit.html",
            campaign=campaign,
            price_max_display=cents_to_dollars_str(campaign.price_max_cents),
            can_delete=_can_manage(campaign) and not _has_pending_actions(campaign),
            **_campaign_form_kwargs(),
        )

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return redirect(url_for("campaigns.campaign_edit", campaign_id=campaign.id))

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
