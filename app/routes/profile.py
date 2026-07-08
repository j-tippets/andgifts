from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from app.services.storage import upload_avatar, delete_avatar, StorageError

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
