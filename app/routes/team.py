import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user, login_user

from app.extensions import db
from app.models import User, ContactAuditLog
from app.models.contact import Contact
from app.decorators import admin_required
from app.services.storage import upload_avatar, delete_avatar, StorageError

team_bp = Blueprint("team", __name__, url_prefix="/team")

INVITE_EXPIRY_DAYS = 7


@team_bp.route("/")
@admin_required
def list_members():
    members = (
        User.query.filter_by(org_id=current_user.org_id)
        .order_by(User.status.desc(), User.created_at)
        .all()
    )
    return render_template(
        "team/list.html",
        members=members,
        org=current_user.org,
        can_add_seat=current_user.org.can_add_seat(),
    )


@team_bp.route("/new", methods=["GET", "POST"])
@admin_required
def new_member():
    org = current_user.org

    if request.method == "GET":
        if not org.can_add_seat():
            flash(
                f"You've hit your plan's seat limit ({org.limit_for('seats')}). "
                f"Upgrade your plan to add another agent.",
                "error",
            )
            return redirect(url_for("team.list_members"))
        return render_template("team/new.html")

    if not org.can_add_seat():
        flash("Seat limit reached for your plan.", "error")
        return redirect(url_for("team.list_members"))

    email = request.form["email"].strip().lower()
    if User.query.filter_by(email=email).first():
        flash("An account with that email already exists.", "error")
        return redirect(url_for("team.new_member"))

    method = request.form.get("method", "invite")  # "invite" or "direct"

    user = User(
        org_id=org.id,
        email=email,
        first_name=request.form.get("first_name", ""),
        last_name=request.form.get("last_name", ""),
        role=request.form.get("role", "agent"),
        invited_by_user_id=current_user.id,
    )

    if method == "direct":
        # Admin sets a temp password right now; agent can change it after login.
        temp_password = request.form.get("temp_password") or secrets.token_urlsafe(9)
        user.set_password(temp_password)
        user.status = "active"
        db.session.add(user)
        db.session.commit()
        flash(
            f"Account created for {email}. Temporary password: {temp_password} "
            f"(share this with them directly -- it won't be shown again).",
            "success",
        )
    else:
        # Email-invite path: account exists in "pending" state until the
        # agent clicks the link and sets their own password.
        # NOTE: actually emailing the link requires SendGrid, which is still
        # deferred -- for now the admin can copy/paste the link manually.
        user.status = "pending"
        user.invite_token = secrets.token_urlsafe(32)
        user.invite_expires_at = datetime.utcnow() + timedelta(days=INVITE_EXPIRY_DAYS)
        db.session.add(user)
        db.session.commit()
        invite_link = url_for("team.accept_invite", token=user.invite_token, _external=True)
        flash(
            f"Invite created for {email}. Send them this link (expires in "
            f"{INVITE_EXPIRY_DAYS} days): {invite_link}",
            "success",
        )

    return redirect(url_for("team.list_members"))


@team_bp.route("/accept/<token>", methods=["GET", "POST"])
def accept_invite(token):
    user = User.query.filter_by(invite_token=token, status="pending").first()
    if not user or not user.invite_expires_at or user.invite_expires_at < datetime.utcnow():
        flash("That invite link is invalid or has expired. Ask your admin to resend it.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form.get("confirm_password")
        if password != confirm:
            flash("Passwords don't match.", "error")
            return render_template("team/accept_invite.html", user=user)

        user.set_password(password)
        user.status = "active"
        user.invite_token = None
        user.invite_expires_at = None
        db.session.commit()

        login_user(user)
        flash(f"Welcome to {user.org.name}!", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("team/accept_invite.html", user=user)


@team_bp.route("/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_member(user_id):
    member = User.query.filter_by(id=user_id, org_id=current_user.org_id).first_or_404()
    owned_contacts_count = Contact.query.filter_by(
        org_id=current_user.org_id, owner_user_id=member.id
    ).count()

    if request.method == "GET":
        return render_template(
            "team/edit.html", member=member, owned_contacts_count=owned_contacts_count
        )

    member.first_name = request.form.get("first_name", "").strip()
    member.last_name = request.form.get("last_name", "").strip()

    new_email = request.form.get("email", "").strip().lower()
    if new_email and new_email != member.email:
        if User.query.filter(User.email == new_email, User.id != member.id).first():
            flash("Another account already uses that email.", "error")
            return redirect(url_for("team.edit_member", user_id=member.id))
        member.email = new_email

    if member.id != current_user.id:
        member.role = request.form.get("role", member.role)

    if request.form.get("remove_photo") == "1" and member.photo_url:
        delete_avatar(member.photo_url)
        member.photo_url = None

    photo = request.files.get("photo")
    if photo and photo.filename:
        try:
            old_photo_url = member.photo_url
            member.photo_url = upload_avatar(photo, member.id)
            if old_photo_url:
                delete_avatar(old_photo_url)
        except StorageError as exc:
            flash(str(exc), "error")
            return redirect(url_for("team.edit_member", user_id=member.id))

    db.session.commit()
    flash(f"{member.full_name}'s profile has been updated.", "success")
    return redirect(url_for("team.list_members"))


@team_bp.route("/<user_id>/delete", methods=["POST"])
@admin_required
def delete_member(user_id):
    member = User.query.filter_by(id=user_id, org_id=current_user.org_id).first_or_404()
    if member.id == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("team.list_members"))

    owned_contacts_count = Contact.query.filter_by(
        org_id=current_user.org_id, owner_user_id=member.id
    ).count()

    if owned_contacts_count and request.form.get("reassign_contacts") != "1":
        flash(
            f"{member.full_name} owns {owned_contacts_count} contact(s). "
            f"Check the confirmation box to unassign them and delete the profile.",
            "error",
        )
        return redirect(url_for("team.edit_member", user_id=member.id))

    if owned_contacts_count:
        Contact.query.filter_by(org_id=current_user.org_id, owner_user_id=member.id).update(
            {"owner_user_id": None}
        )

    # Clear self-referential invite attribution so the FK doesn't block delete.
    User.query.filter_by(invited_by_user_id=member.id).update({"invited_by_user_id": None})

    # Same for the contact audit trail -- keep the history (via actor_name_snapshot)
    # but detach it from this user's id so the FK doesn't block delete.
    ContactAuditLog.query.filter_by(actor_user_id=member.id).update({"actor_user_id": None})

    if member.photo_url:
        delete_avatar(member.photo_url)

    name = member.full_name
    db.session.delete(member)
    db.session.commit()
    flash(f"{name}'s profile has been permanently deleted.", "success")
    return redirect(url_for("team.list_members"))


@team_bp.route("/<user_id>/disable", methods=["POST"])
@admin_required
def disable_member(user_id):
    user = User.query.filter_by(id=user_id, org_id=current_user.org_id).first_or_404()
    if user.id == current_user.id:
        flash("You can't disable your own account.", "error")
        return redirect(url_for("team.list_members"))
    user.status = "disabled"
    db.session.commit()
    flash(f"{user.full_name}'s access has been disabled.", "success")
    return redirect(url_for("team.list_members"))


@team_bp.route("/<user_id>/reactivate", methods=["POST"])
@admin_required
def reactivate_member(user_id):
    org = current_user.org
    if not org.can_add_seat():
        flash("Seat limit reached for your plan -- upgrade to reactivate this account.", "error")
        return redirect(url_for("team.list_members"))
    user = User.query.filter_by(id=user_id, org_id=current_user.org_id).first_or_404()
    user.status = "active"
    db.session.commit()
    flash(f"{user.full_name}'s access has been restored.", "success")
    return redirect(url_for("team.list_members"))
