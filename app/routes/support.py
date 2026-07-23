from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import SupportRequest
from app.services.email import send_support_request

support_bp = Blueprint("support", __name__, url_prefix="/support")

TOPICS = [
    "Billing",
    "Bug report",
    "Feature request",
    "Account / login",
    "Gift orders",
    "Other",
]


@support_bp.route("/", methods=["GET", "POST"])
@login_required
def contact():
    """
    Support form: user picks a topic and describes the issue. We email the
    internal support inbox and log a SupportRequest row either way (so a
    submission is never lost even if the email itself fails to send --
    email_delivered records which happened).
    """
    if request.method == "GET":
        return render_template("support/contact.html", topics=TOPICS)

    topic = request.form.get("topic", "").strip()
    message = request.form.get("message", "").strip()

    if not topic or not message:
        flash("Please choose a topic and describe the issue.", "error")
        return redirect(url_for("support.contact"))

    delivered = send_support_request(current_user, topic, message)

    db.session.add(SupportRequest(
        org_id=current_user.org_id,
        org_name_snapshot=current_user.org.name if current_user.org else "(no org)",
        user_id=current_user.id,
        user_name_snapshot=current_user.full_name,
        user_email_snapshot=current_user.email,
        topic=topic,
        message=message,
        email_delivered=delivered,
    ))
    db.session.commit()

    if delivered:
        flash("Thanks -- your message has been sent to our support team.", "success")
    else:
        flash(
            "We couldn't send your message right now. Please try again shortly.",
            "error",
        )
    return redirect(url_for("support.contact"))
