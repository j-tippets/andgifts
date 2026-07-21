import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required
from app.extensions import db
from app.models import User, Org
from app.services.email import send_verification_email, send_password_reset_email

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

VERIFY_EXPIRY_HOURS = 48
RESET_EXPIRY_MINUTES = 60


def _send_verification(user):
    """(Re)issues a verification token/expiry for `user` and emails it.
    Shared by register() and resend_verification() so both stay in sync."""
    user.email_verify_token = secrets.token_urlsafe(32)
    user.email_verify_expires_at = datetime.utcnow() + timedelta(hours=VERIFY_EXPIRY_HOURS)
    db.session.commit()
    verify_link = url_for("auth.verify_email", token=user.email_verify_token, _external=True)
    return send_verification_email(user, verify_link)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("auth.register"))

        org = Org(name=request.form.get("org_name", "My Business"), tier="free")
        db.session.add(org)
        db.session.flush()  # get org.id before creating user

        user = User(
            org_id=org.id,
            email=email,
            first_name=request.form.get("first_name", ""),
            last_name=request.form.get("last_name", ""),
            role="admin",
            email_verified=False,
        )
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()

        delivered = _send_verification(user)
        if not delivered:
            flash(
                "Account created, but we couldn't send the verification email. "
                "Try resending it below once things are set up.",
                "error",
            )
        return render_template("auth/check_email.html", email=user.email, purpose="verify")

    return render_template("auth/register.html")


@auth_bp.route("/verify/<token>")
def verify_email(token):
    user = User.query.filter_by(email_verify_token=token).first()
    if not user or not user.email_verify_expires_at or user.email_verify_expires_at < datetime.utcnow():
        flash("That verification link is invalid or has expired. Request a new one below.", "error")
        return redirect(url_for("auth.resend_verification"))

    user.email_verified = True
    user.email_verify_token = None
    user.email_verify_expires_at = None
    db.session.commit()

    login_user(user)
    flash(f"Welcome to {user.org.name}!", "success")
    return redirect(url_for("dashboard.index"))


@auth_bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = User.query.filter_by(email=email).first()
        # Same message whether or not the account exists / is already
        # verified, so this can't be used to probe which emails are
        # registered.
        if user and user.status == "active" and not user.email_verified:
            _send_verification(user)
        flash("If that account needs verifying, we've sent a new link.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/resend_verification.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form["password"]):
            if login_user(user):
                return redirect(url_for("dashboard.index"))
            # Password was correct but the account can't log in yet --
            # give a specific reason instead of a generic error.
            if not user.email_verified:
                flash(
                    "Please verify your email before signing in. "
                    "Didn't get the link? Use the resend option below.",
                    "error",
                )
            elif user.status == "pending":
                flash("Check your email for an invite link to activate your account.", "error")
            elif user.status == "disabled":
                flash("This account has been disabled. Contact your admin.", "error")
            else:
                flash("This account can't sign in right now.", "error")
        else:
            flash("Invalid email or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = User.query.filter_by(email=email).first()
        # Only issue a reset for accounts that can actually use it, but
        # always show the same message either way -- don't leak whether
        # an email is registered.
        if user and user.status == "active":
            user.reset_token = secrets.token_urlsafe(32)
            user.reset_expires_at = datetime.utcnow() + timedelta(minutes=RESET_EXPIRY_MINUTES)
            db.session.commit()
            reset_link = url_for("auth.reset_password", token=user.reset_token, _external=True)
            send_password_reset_email(user, reset_link)
        flash("If an account exists for that email, we've sent a password reset link.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_expires_at or user.reset_expires_at < datetime.utcnow():
        flash("That reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form.get("confirm_password")
        if password != confirm:
            flash("Passwords don't match.", "error")
            return render_template("auth/reset_password.html", token=token)

        user.set_password(password)
        user.reset_token = None
        user.reset_expires_at = None
        # Clicking a link mailed to this address proves ownership of it,
        # same as email verification would -- so clear that gate too,
        # rather than leaving a legitimate user stuck unverified.
        user.email_verified = True
        db.session.commit()

        login_user(user)
        flash("Your password has been reset.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/reset_password.html", token=token)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
