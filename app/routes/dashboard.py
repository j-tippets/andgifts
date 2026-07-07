from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import SuggestedAction, ActionLog
from app.services.suggestion_engine import generate_suggestions_for_org

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    org = current_user.org

    # For MVP we generate on-demand rather than requiring a cron job to have
    # already run. Cheap because it's idempotent (skips dates already covered).
    if org.feature_enabled("ai_dashboard"):
        generate_suggestions_for_org(org)

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
    action.status = "approved"
    action.resolved_at = datetime.utcnow()

    # MVP: log it immediately. Real send (email/SMS/gift fulfillment API call)
    # gets wired in as its own service once channels are built.
    db.session.add(ActionLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        suggested_action_id=action.id,
        action_type=action.action_type,
        detail=action.reason_text,
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
