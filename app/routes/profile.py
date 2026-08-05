from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from app.services.storage import upload_avatar, delete_avatar, StorageError
from app.services.sendgrid_sender import (
    create_sender_identity, get_sender_status, resend_verification,
)

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")

MIN_PASSWORD_LENGTH = 8


@profile_bp.route("/", methods=["GET", "POST"])
@login_required
def edit_profile():
    """
    Self-service profile editing: name, email, photo, password.
    Deliberately does NOT touch role, status, or account deletion --
    those stay admin-only via the /team routes.
    """
    if request.method == "GET":
        # Passive poll: there's no verification webhook (see
        # app/services/sendgrid_sender.py), so a still-pending sender
        # identity gets its status refreshed here on every page load,
        # in addition to the manual "recheck" button below. Best-effort
        # -- if SendGrid can't be reached, the page just shows whatever
        # status is already on file.
        if current_user.sendgrid_sender_id and not current_user.sender_verified:
            status = get_sender_status(current_user.sendgrid_sender_id)
            if status is True:
                current_user.sender_verified = True
                db.session.commit()
        return render_template("profile/edit.html")

    current_user.first_name = request.form.get("first_name", "").strip()
    current_user.last_name = request.form.get("last_name", "").strip()

    new_email = request.form.get("email", "").strip().lower()
    if new_email and new_email != current_user.email:
        if User.query.filter(User.email == new_email, User.id != current_user.id).first():
            flash("Another account already uses that email.", "error")
            return redirect(url_for("profile.edit_profile"))
        current_user.email = new_email

    if request.form.get("remove_photo") == "1" and current_user.photo_url:
        delete_avatar(current_user.photo_url)
        current_user.photo_url = None

    photo = request.files.get("photo")
    if photo and photo.filename:
        try:
            old_photo_url = current_user.photo_url
            current_user.photo_url = upload_avatar(photo, current_user.id)
            if old_photo_url:
                delete_avatar(old_photo_url)
        except StorageError as exc:
            flash(str(exc), "error")
            return redirect(url_for("profile.edit_profile"))

    new_password = request.form.get("new_password", "")
    if new_password:
        current_password = request.form.get("current_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("profile.edit_profile"))
        if new_password != confirm_password:
            flash("New passwords don't match.", "error")
            return redirect(url_for("profile.edit_profile"))
        if len(new_password) < MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
            return redirect(url_for("profile.edit_profile"))
        current_user.set_password(new_password)

    db.session.commit()
    flash("Your profile has been updated.", "success")
    return redirect(url_for("profile.edit_profile"))


@profile_bp.route("/sender", methods=["POST"])
@login_required
def set_sender_identity():
    """Registers (or replaces) this agent's outbound sender identity with
    SendGrid. A changed email is always a brand-new SendGrid identity --
    not an edit to the old one -- since the new address needs its own
    verification click regardless of what the old one's status was."""
    email = request.form.get("sender_email", "").strip().lower()
    name = request.form.get("sender_display_name", "").strip() or current_user.full_name
    address = request.form.get("sender_address", "").strip()
    city = request.form.get("sender_city", "").strip()
    state = request.form.get("sender_state", "").strip()
    zip_code = request.form.get("sender_zip", "").strip()
    country = request.form.get("sender_country", "").strip() or "United States"

    if not email:
        flash("Enter an email address to verify.", "error")
        return redirect(url_for("profile.edit_profile"))

    if not address or not city or not zip_code:
        flash(
            "SendGrid requires a physical mailing address on file for every sender "
            "(this is a CAN-SPAM requirement, not something we're adding) -- "
            "fill in address, city, and ZIP.",
            "error",
        )
        return redirect(url_for("profile.edit_profile"))

    sendgrid_id = create_sender_identity(email, name, address, city, state, zip_code, country)
    if not sendgrid_id:
        flash(
            "Couldn't start verification with SendGrid. Check the app logs, or try again in a moment.",
            "error",
        )
        return redirect(url_for("profile.edit_profile"))

    current_user.sender_email = email
    current_user.sender_name = name
    current_user.sendgrid_sender_id = sendgrid_id
    current_user.sender_verified = False
    db.session.commit()

    flash(f"Check {email} for a confirmation link from SendGrid.", "success")
    return redirect(url_for("profile.edit_profile"))


@profile_bp.route("/sender/recheck", methods=["POST"])
@login_required
def recheck_sender_identity():
    """Manual fallback to the passive poll on GET /profile -- lets an
    agent confirm verification immediately after clicking the email
    link, without waiting for their next page load."""
    if not current_user.sendgrid_sender_id:
        flash("No sender verification in progress.", "error")
        return redirect(url_for("profile.edit_profile"))

    status = get_sender_status(current_user.sendgrid_sender_id)
    if status is True:
        current_user.sender_verified = True
        db.session.commit()
        flash("Your sender email is verified.", "success")
    elif status is False:
        flash("Still pending -- click the confirmation link SendGrid emailed you.", "info")
    else:
        flash("Couldn't check status with SendGrid right now. Try again shortly.", "error")
    return redirect(url_for("profile.edit_profile"))


@profile_bp.route("/sender/resend", methods=["POST"])
@login_required
def resend_sender_verification():
    """Re-triggers SendGrid's confirmation email, for an agent who lost
    the original or let it expire."""
    if not current_user.sendgrid_sender_id:
        flash("No sender verification in progress.", "error")
        return redirect(url_for("profile.edit_profile"))

    if resend_verification(current_user.sendgrid_sender_id):
        flash(f"Verification email resent to {current_user.sender_email}.", "success")
    else:
        flash("Couldn't resend the verification email. Try again shortly.", "error")
    return redirect(url_for("profile.edit_profile"))
