from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Campaign, CampaignRecipe, User

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")


def _can_manage(campaign):
    """Org admins can manage anything in their org (agency-wide or any
    agent's personal campaign). A regular agent can only manage their
    own personal campaigns -- never an agency-wide one."""
    if campaign.org_id != current_user.org_id:
        return False
    if current_user.is_admin:
        return True
    return campaign.owner_user_id == current_user.id


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
