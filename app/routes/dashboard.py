from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import SuggestedAction, ActionLog, ContactAuditLog
from app.services.suggestion_engine import generate_suggestions_for_org, generate_campaign_suggestions_for_org
from app.services.email import send_flow_action_email

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
        if action.generated_message:
            detail += f" \u2014 note: {action.generated_message}"
        cost_cents = action.suggested_gift.price_cents
    else:
        detail = action.generated_message or action.reason_text
        cost_cents = None

    # "email" is the first action type wired up to an actual send (the
    # others -- gift fulfillment, text, handwritten_note -- are still
    # manual for now). A failed send does NOT block the approval or roll
    # anything back: the agent's decision to approve stands, we just
    # record that it didn't go out automatically so it surfaces in the
    # reports and they know to follow up by hand.
    delivery_status = None
    delivery_error = None
    if action.action_type == "email":
        delivered, error = send_flow_action_email(action, current_user.full_name)
        delivery_status = "sent" if delivered else "failed"
        delivery_error = error

    # MVP: log it immediately. Real send (email/SMS/gift fulfillment API call)
    # gets wired in as its own service once channels are built.
    db.session.add(ActionLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        suggested_action_id=action.id,
        action_type=action.action_type,
        detail=detail,
        cost_cents=cost_cents,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
    ))
    audit_summary = _action_summary_for_log(action, "Approved")
    if delivery_status == "failed":
        audit_summary += f" Email did not send automatically: {delivery_error}"

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_approved",
        summary=audit_summary,
        suggested_action_id=action.id,
    ))
    db.session.commit()
    if delivery_status == "failed":
        flash(f"Approved, but the email didn't send automatically: {delivery_error}", "error")
    else:
        flash("Action approved and queued.", "success")
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/skip", methods=["POST"])
@login_required
def skip_action(action_id):
    action = SuggestedAction.query.filter_by(id=action_id, org_id=current_user.org_id).first_or_404()
    action.status = "skipped"
    action.resolved_at = datetime.utcnow()
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/delete", methods=["POST"])
@login_required
def delete_action(action_id):
    """Hides the action and stops THIS occurrence from ever regenerating
    (the (contact, event, target_date) tuple stays taken -- see
    _suggestion_exists / _campaign_suggestion_exists in the suggestion
    engine), but does NOT block a future occurrence of a recurring event:
    a deleted purchase-anniversary gift this year still lets the contact
    qualify for next year's anniversary. Logged to the contact's activity
    feed so it can be undone from there if it was a mistake."""
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
    flash("Deleted. It won't reappear for this occurrence, but the contact can still qualify next time.", "success")
    return redirect(request.referrer or url_for("dashboard.index"))


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


@dashboard_bp.route("/actions/<action_id>/unapprove", methods=["POST"])
@login_required
def unapprove_action(action_id):
    """Undoes an approval: puts the suggestion back to pending (so it's
    editable again via edit_action and reappears as an active-event card
    on the Today tab / contact page), and removes the ActionLog row that
    approve_action created -- that row is the permanent spend/tax record,
    and it shouldn't survive an undone approval, both because it was
    never accurate to begin with and because re-approving later would
    otherwise create a second, duplicate ActionLog entry for the same
    suggestion. Only called from the contact's Recent Activity list,
    next to the action_approved entry it's undoing -- same pattern as
    undelete_action next to action_deleted."""
    action = SuggestedAction.query.filter_by(
        id=action_id, org_id=current_user.org_id, status="approved"
    ).first_or_404()

    ActionLog.query.filter_by(suggested_action_id=action.id).delete(synchronize_session=False)

    action.status = "pending"
    action.resolved_at = None

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_unapproved",
        summary=_action_summary_for_log(action, "Un-approved"),
        suggested_action_id=action.id,
    ))
    db.session.commit()
    flash("Approval undone. It's back to pending -- edit it and re-approve when it's ready.", "success")
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

    if "generated_message" in request.form:
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
