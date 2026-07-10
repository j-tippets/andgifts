from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Campaign, CampaignRecipe, User, SuggestedAction
from app.models.timeline import STANDARD_EVENT_TYPES
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")


def _can_manage(campaign):
    """Org admins can manage anything in their org (agency-wide or any
    agent's personal campaign). A regular agent can only manage their
    own personal campaigns -- never a team-wide one."""
    if campaign.org_id != current_user.org_id:
        return False
    if current_user.is_admin:
        return True
    return campaign.owner_user_id == current_user.id


def _has_pending_actions(campaign):
    """True if this personal flow still has pending suggestions sitting
    on someone's dashboard. Only meaningful for personal flows -- a
    team-wide flow never generates suggestions directly, so it's always
    safe to delete."""
    return db.session.query(
        SuggestedAction.query.filter_by(source_campaign_id=campaign.id, status="pending").exists()
    ).scalar()


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

    agency_wide = (
        Campaign.query.filter_by(org_id=org_id, owner_user_id=None)
        .order_by(Campaign.name)
        .all()
    )
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
        agency_wide=agency_wide,
        my_campaigns=my_campaigns,
        other_personal_by_agent=other_personal_by_agent,
    )


@campaigns_bp.route("/book")
@login_required
def recipe_book():
    recipes = CampaignRecipe.query.filter_by(is_active=True).order_by(CampaignRecipe.name).all()
    return render_template("campaigns/book.html", recipes=recipes)


@campaigns_bp.route("/book/<recipe_id>/add", methods=["POST"])
@login_required
def add_from_recipe(recipe_id):
    recipe = CampaignRecipe.query.filter_by(id=recipe_id, is_active=True).first_or_404()
    scope = request.form.get("scope", "personal")

    if scope == "agency" and not current_user.is_admin:
        flash("Only an agency admin can add an agency-wide campaign.", "error")
        return redirect(url_for("campaigns.recipe_book"))

    owner_user_id = None if scope == "agency" else current_user.id
    campaign = Campaign.from_recipe(
        recipe,
        org_id=current_user.org_id,
        owner_user_id=owner_user_id,
        created_by_user_id=current_user.id,
    )
    db.session.add(campaign)
    db.session.commit()

    flash(
        f"Added \u201c{campaign.name}\u201d as {'an agency-wide campaign' if owner_user_id is None else 'your personal campaign'}.",
        "success",
    )
    return redirect(url_for("campaigns.list_campaigns"))


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
    """Build a flow from scratch. Admins can choose team-wide (for the
    whole org) or personal (just for themselves); everyone else always
    gets a personal flow."""
    if request.method == "GET":
        return render_template("campaigns/new.html", **_campaign_form_kwargs())

    scope = request.form.get("scope", "personal")
    if scope == "agency" and not current_user.is_admin:
        flash("Only an agency admin can create a team-wide flow.", "error")
        return redirect(url_for("campaigns.list_campaigns"))

    if not request.form.get("name", "").strip():
        flash("Name is required.", "error")
        return render_template("campaigns/new.html", **_campaign_form_kwargs())

    campaign = Campaign(
        org_id=current_user.org_id,
        owner_user_id=None if scope == "agency" else current_user.id,
        created_by_user_id=current_user.id,
        is_active=True,
    )
    _save_campaign_from_form(campaign)
    db.session.add(campaign)
    db.session.commit()

    flash(
        f"Created \u201c{campaign.name}\u201d as {'a team-wide flow' if campaign.owner_user_id is None else 'your personal flow'}.",
        "success",
    )
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
            can_delete=_can_manage(campaign) and (campaign.owner_user_id is None or not _has_pending_actions(campaign)),
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

    # Team-wide flows never generate suggestions directly (only personal
    # copies forked from them do), so deleting one is always safe --
    # existing personal copies just lose their "forked from" breadcrumb.
    # A personal flow, though, may still have live pending suggestions on
    # someone's dashboard; make them resolve those first rather than
    # silently orphaning a card mid-flight.
    if campaign.owner_user_id is not None and _has_pending_actions(campaign):
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


@campaigns_bp.route("/<campaign_id>/add-to-mine", methods=["POST"])
@login_required
def add_from_campaign(campaign_id):
    """Fork a team-wide flow into the current user's own personal copy.
    This is the moment the copy is made -- from here on it's fully
    independent, so the agency admin can edit or delete the team-wide
    original without it touching this copy at all."""
    master = Campaign.query.filter_by(
        id=campaign_id, org_id=current_user.org_id, owner_user_id=None, is_active=True
    ).first_or_404()

    already_added = Campaign.query.filter_by(
        org_id=current_user.org_id,
        owner_user_id=current_user.id,
        forked_from_campaign_id=master.id,
        is_active=True,
    ).first()
    if already_added:
        flash(f"You've already added \u201c{master.name}\u201d to your profile.", "error")
        return redirect(url_for("campaigns.list_campaigns"))

    copy = Campaign.from_campaign(master, owner_user_id=current_user.id, created_by_user_id=current_user.id)
    db.session.add(copy)
    db.session.commit()
    flash(f"Added \u201c{copy.name}\u201d to your profile.", "success")
    return redirect(url_for("campaigns.list_campaigns"))
