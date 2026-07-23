from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

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
    Support form: user picks a topic and describes the issue, we email
    the internal support inbox with their company/name/email/topic/message.
    No ticket storage yet -- this is just a mailer. If volume grows enough
    to need tracking/replies, revisit as a real ticket model.
    """
    if request.method == "GET":
        return render_template("support/contact.html", topics=TOPICS)

    topic = request.form.get("topic", "").strip()
    message = request.form.get("message", "").strip()

    if not topic or not message:
        flash("Please choose a topic and describe the issue.", "error")
        return redirect(url_for("support.contact"))

    delivered = send_support_request(current_user, topic, message)
    if delivered:
        flash("Thanks -- your message has been sent to our support team.", "success")
    else:
        flash(
            "We couldn't send your message right now. Please try again shortly.",
            "error",
        )
    return redirect(url_for("support.contact"))
