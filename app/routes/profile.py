from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user, logout_user

from app.extensions import db
from app.models import User, Contact, ContactAuditLog
from app.services.storage import upload_avatar, delete_avatar, StorageError
from app.services.account_deletion import delete_org_completely

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")

MIN_PASSWORD_LENGTH = 8


@profile_bp.route("/", methods=["GET", "POST"])
@login_required
def edit_profile():
    """
    Self-service profile editing: name, email, photo, password.
    Deliberately does NOT touch role or status -- those stay
    admin-only via the /team routes. Account deletion (below) IS
    self-service, unlike role/status, since a person is always allowed
    to close their own account regardless of what an org admin thinks.
    """
    if request.method == "GET":
        org = current_user.org
        owned_contacts_count = Contact.query.filter_by(
            org_id=current_user.org_id, owner_user_id=current_user.id
        ).count()
        is_sole_user = len(org.users) == 1
        blocked_as_last_admin = False
        if not is_sole_user and current_user.is_admin:
            other_active_admins = [
                u for u in org.users
                if u.id != current_user.id and u.role == "admin" and u.status == "active"
            ]
            blocked_as_last_admin = not other_active_admins
        return render_template(
            "profile/edit.html",
            owned_contacts_count=owned_contacts_count,
            is_sole_user=is_sole_user,
            blocked_as_last_admin=blocked_as_last_admin,
        )

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


@profile_bp.route("/delete", methods=["POST"])
@login_required
def delete_account():
    """Self-service account closure -- required for app store review
    (Apple 5.1.1v: any app that supports account creation must support
    in-app account deletion), and just basic fairness regardless of
    platform. Password-gated since this is irreversible.

    Two very different outcomes depending on org shape:
    - Sole user in the org: the org itself is meaningless without
      them, so the whole org and every row scoped to it is deleted
      (see delete_org_completely).
    - One of several users: only their own User row is removed,
      mirroring team.delete_member's handling of owned contacts and
      dangling references -- the org and everyone else keep working.
      An admin can't take this path if they're the org's only active
      admin, since that would strand the remaining agents with no one
      able to manage the team; they need to promote someone first.
    """
    if not current_user.check_password(request.form.get("current_password", "")):
        flash("Incorrect password.", "error")
        return redirect(url_for("profile.edit_profile"))

    org = current_user.org

    if len(org.users) == 1:
        delete_org_completely(org)
        logout_user()
        flash("Your account and all its data have been permanently deleted.", "success")
        return redirect(url_for("auth.login"))

    if current_user.is_admin:
        other_active_admins = [
            u for u in org.users
            if u.id != current_user.id and u.role == "admin" and u.status == "active"
        ]
        if not other_active_admins:
            flash(
                "You're the only admin for this team -- promote another teammate to "
                "admin in Team settings before deleting your account.",
                "error",
            )
            return redirect(url_for("profile.edit_profile"))

    owned_contacts_count = Contact.query.filter_by(
        org_id=current_user.org_id, owner_user_id=current_user.id
    ).count()
    if owned_contacts_count and request.form.get("reassign_contacts") != "1":
        flash(
            f"You own {owned_contacts_count} contact(s). Check the confirmation box "
            f"to unassign them and delete your account.",
            "error",
        )
        return redirect(url_for("profile.edit_profile"))

    if owned_contacts_count:
        Contact.query.filter_by(
            org_id=current_user.org_id, owner_user_id=current_user.id
        ).update({"owner_user_id": None})

    User.query.filter_by(invited_by_user_id=current_user.id).update({"invited_by_user_id": None})
    ContactAuditLog.query.filter_by(actor_user_id=current_user.id).update({"actor_user_id": None})

    if current_user.photo_url:
        delete_avatar(current_user.photo_url)

    user_id = current_user.id
    logout_user()
    User.query.filter_by(id=user_id).delete()
    db.session.commit()
    flash("Your account has been permanently deleted.", "success")
    return redirect(url_for("auth.login"))
