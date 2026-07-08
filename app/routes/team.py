import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user, login_user

from app.extensions import db
from app.models import User
from app.decorators import admin_required

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
