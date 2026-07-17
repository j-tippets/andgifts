from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import SuggestedAction, ActionLog, ContactAuditLog
from app.services.suggestion_engine import generate_suggestions_for_org, generate_campaign_suggestions_for_org

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    org = current_user.org

    # For MVP we generate on-demand rather than requiring a cron job to have
    # already run. Cheap because it's idempotent (skips dates already covered).
    if org.feature_enabled("ai_dashboard"):
        generate_suggestions_for_org(org)
        generate_campaign_suggestions_for_org(org)

    pending = (
        SuggestedAction.query
        .filter_by(org_id=org.id, status="pending")
        .order_by(SuggestedAction.target_date)
        .all()
    )
    return render_template("dashboard/index.html", suggestions=pending, ai_enabled=org.feature_enabled("ai_dashboard"))


@dashboard_bp.route("/actions/<action_id>/approve", methods=["POST"])
@login_required
def approve_action(action_id):
    action = SuggestedAction.query.filter_by(id=action_id, org_id=current_user.org_id).first_or_404()

    # Gift suggestions can be swapped for a different catalog item right from
    # the dashboard before approving -- only trust an id that's actually
    # available to this org (respects catalog curation).
    if action.action_type == "gift":
        chosen_gift_id = request.form.get("gift_catalog_item_id", "").strip()
        if chosen_gift_id:
            available_ids = {g.id for g in current_user.org.available_catalog_items()}
            if chosen_gift_id in available_ids:
                action.suggested_gift_id = chosen_gift_id

    action.status = "approved"
    action.resolved_at = datetime.utcnow()

    if action.action_type == "gift" and action.suggested_gift:
        detail = f"{action.suggested_gift.name} (${action.suggested_gift.price_cents / 100:.2f})"
        cost_cents = action.suggested_gift.price_cents
    else:
        detail = action.generated_message or action.reason_text
        cost_cents = None

    # MVP: log it immediately. Real send (email/SMS/gift fulfillment API call)
    # gets wired in as its own service once channels are built.
    db.session.add(ActionLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        suggested_action_id=action.id,
        action_type=action.action_type,
        detail=detail,
        cost_cents=cost_cents,
    ))
    db.session.commit()
    flash("Action approved and queued.", "success")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/skip", methods=["POST"])
@login_required
def skip_action(action_id):
    action = SuggestedAction.query.filter_by(id=action_id, org_id=current_user.org_id).first_or_404()
    action.status = "skipped"
    action.resolved_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/delete", methods=["POST"])
@login_required
def delete_action(action_id):
    """Stronger than skip: hides the action AND stops it from ever being
    suggested again, including future occurrences of a recurring event
    (see _permanently_deleted / _campaign_permanently_deleted in the
    suggestion engine). Logged to the contact's activity feed so it can be
    undone from there if it was a mistake."""
    action = SuggestedAction.query.filter_by(id=action_id, org_id=current_user.org_id).first_or_404()
    action.status = "deleted"
    action.resolved_at = datetime.utcnow()

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_deleted",
        summary=_action_summary_for_log(action, "Deleted"),
        suggested_action_id=action.id,
    ))
    db.session.commit()
    flash("Deleted. This won't be suggested again.", "success")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/undelete", methods=["POST"])
@login_required
def undelete_action(action_id):
    """Restores a deleted suggestion back to pending -- called from the
    contact's recent-activity list, not from the Today tab."""
    action = SuggestedAction.query.filter_by(
        id=action_id, org_id=current_user.org_id, status="deleted"
    ).first_or_404()
    action.status = "pending"
    action.resolved_at = None

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_undeleted",
        summary=_action_summary_for_log(action, "Restored"),
        suggested_action_id=action.id,
    ))
    db.session.commit()
    flash("Restored. It's back on the Today tab.", "success")
    return redirect(request.referrer or url_for("contacts.view_contact", contact_id=action.contact_id))


@dashboard_bp.route("/actions/<action_id>/edit", methods=["POST"])
@login_required
def edit_action(action_id):
    """Lets the agent fix the LLM's copy (email/text/handwritten_note) or
    swap the gift before ever approving/deleting it. Saves in place and
    stays pending -- this is deliberately separate from approve, which is
    still required to actually queue/send it."""
    action = SuggestedAction.query.filter_by(id=action_id, org_id=current_user.org_id).first_or_404()
    if action.status != "pending":
        flash("Only pending suggestions can be edited.", "error")
        return redirect(url_for("dashboard.index"))

    if action.action_type == "gift":
        chosen_gift_id = request.form.get("gift_catalog_item_id", "").strip()
        if chosen_gift_id:
            available_ids = {g.id for g in current_user.org.available_catalog_items()}
            if chosen_gift_id in available_ids:
                action.suggested_gift_id = chosen_gift_id
    else:
        new_message = request.form.get("generated_message", "").strip()
        action.generated_message = new_message or None

    db.session.commit()
    flash("Changes saved.", "success")
    return redirect(url_for("dashboard.index"))


def _action_summary_for_log(action, verb):
    kind = action.action_type.replace("_", " ")
    if action.action_type == "gift" and action.suggested_gift:
        return f"{verb} suggested gift \u2014 {action.suggested_gift.name} \u2014 for {action.contact.household_name}."
    return f"{verb} suggested {kind} for {action.contact.household_name}."
