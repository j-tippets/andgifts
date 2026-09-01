import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required
from markupsafe import Markup, escape
from app.extensions import db, limiter
from app.models import User
from app.services.email import send_verification_email, send_password_reset_email
from app.services.analytics import queue_event

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
    """Deprecated: superseded by the multi-step onboarding wizard
    (see routes/onboarding.py). Kept as a redirect rather than removed
    outright so old bookmarks/links (app store review notes, etc.)
    still land somewhere that works."""
    return redirect(url_for("onboarding.start"))


@auth_bp.route("/verify/<token>", methods=["GET", "POST"])
def verify_email(token):
    user = User.query.filter_by(email_verify_token=token).first()
    if not user or not user.email_verify_expires_at or user.email_verify_expires_at < datetime.utcnow():
        flash("That verification link is invalid or has expired. Request a new one below.", "error")
        return redirect(url_for("auth.resend_verification"))

    # Only confirm on POST (an actual human clicking the button below) --
    # NOT on GET. Outlook/Microsoft Defender's "Safe Links" automatically
    # visits every link in an email at delivery time to scan it for
    # malware, before a person ever sees the inbox. That automated visit
    # was hitting this route as a plain GET and silently burning the
    # one-time token, so the real click minutes later always found it
    # already used. A bot doing an automated GET won't submit a form.
    if request.method == "POST":
        user.email_verified = True
        user.email_verify_token = None
        user.email_verify_expires_at = None
        db.session.commit()

        login_user(user)

        # Verification now fires right after Step 1 of the signup
        # wizard (see routes/onboarding.start), so this link can land
        # while the wizard is still in progress -- possibly in a
        # different browser/tab than the one running it. Resume there
        # instead of dumping them at the dashboard; re-seed
        # session["onboarding"] ourselves since that's what the wizard
        # routes key off of, not current_user.
        resume_route = user.org.onboarding_route()
        if resume_route:
            session["onboarding"] = {"org_id": user.org_id, "user_id": user.id}
            # Built with Markup (not a plain string) so the link below
            # survives base.html's `{{ message }}` render un-escaped --
            # safe here since nothing in this message is user-supplied.
            # The redirect below already lands the person on
            # `resume_route`, but a visible, clickable next step in the
            # message itself avoids ever looking like a dead end -- e.g.
            # if they'd already left that tab open on an older page, or
            # just don't notice the page changed underneath the banner.
            continue_url = url_for(resume_route)
            flash(
                Markup(
                    "Email verified! Let's finish setting up your account. "
                    '<a href="%s">Continue where you left off &rarr;</a>'
                ) % continue_url,
                "success",
            )
            return redirect(url_for(resume_route))

        flash(f"Welcome to {user.org.name}!", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/verify_email.html", token=token)


@auth_bp.route("/resend-verification", methods=["GET", "POST"])
@limiter.limit("5 per hour")
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
@limiter.limit("10 per minute;50 per hour")
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form["password"]):
            if login_user(user):
                queue_event("login", method="email")
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
@limiter.limit("5 per hour")
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
