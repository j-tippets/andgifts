"""
Multi-step signup wizard: replaces the old single-page auth.register
form. Walks a new account through business basics, company type
(vertical), plan selection (Free / Solo / Team), and -- Team only --
a subscription card on file plus up to 5 team-member invites, before
handing off to the existing email-verification flow.

The Org + owner User are created at the end of Step 1 (same as the
old register()), then updated in place as the wizard progresses --
NOT deferred to a final "submit everything" step. This keeps a raw
password out of the session (only ever touches set_password() once,
immediately) and means an abandoned wizard just leaves an
unverified, un-invited org sitting on Free -- the same shape as
someone abandoning the old single-page form, not a new failure mode.

session["onboarding"] holds only non-sensitive identifiers
(org_id, user_id) across steps -- never the password. Each step
requires those to be present, or bounces back to Step 1.
"""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from flask_login import current_user

from app.extensions import db, limiter
from app.models import User, Org, PracticeType
from app.services.email import send_team_invite_email
from app.services.org_events import record_org_event
from app.services.practice_types import seed_org_milestones
from app.services.stripe_client import get_stripe
from app.services import org_billing
from app.routes.auth import _send_verification  # reuse register()'s verification-send helper
from app.routes.team import INVITE_EXPIRY_DAYS

onboarding_bp = Blueprint("onboarding", __name__, url_prefix="/get-started")

# Tiers this wizard offers at signup. Pro is deliberately excluded --
# it's a custom-outreach upsell today, not a self-serve signup choice
# (see settings/billing.html, which still offers it as an upgrade
# path once an org already exists).
WIZARD_TIERS = ("free", "starter", "team")

MAX_WIZARD_INVITES = 5


def _wizard_org_user():
    """Loads the in-progress (org, user) pair from session, or
    (None, None) if this browser has no wizard in flight -- callers
    redirect to Step 1 in that case."""
    org_id = session.get("onboarding", {}).get("org_id")
    user_id = session.get("onboarding", {}).get("user_id")
    if not org_id or not user_id:
        return None, None
    org = Org.query.get(org_id)
    user = User.query.get(user_id)
    if not org or not user or user.org_id != org.id:
        return None, None
    return org, user


def _require_wizard(next_step_if_missing="onboarding.start"):
    org, user = _wizard_org_user()
    if not org or not user:
        flash("Let's start from the beginning.", "error")
        return None, None, redirect(url_for(next_step_if_missing))
    return org, user, None


@onboarding_bp.route("/", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def start():
    """Step 1: business basics. Creates the Org + owner User
    immediately on submit (tier defaults to 'free' until Step 3 sets
    the real choice), matching auth.register's existing validation."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    # Optional ?tier=team (from the pricing page's Team CTA) -- carried
    # through session so Step 3 can preselect it instead of defaulting
    # to Free. Purely a UX nicety; the wizard still lets them change it.
    preselect_tier = request.args.get("tier")
    if preselect_tier in WIZARD_TIERS:
        session["onboarding_preselect_tier"] = preselect_tier

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("onboarding.start"))

        org = Org(name=request.form.get("org_name", "My Business"), tier="free")
        db.session.add(org)
        db.session.flush()
        org.sender_local_part = Org.generate_sender_local_part(org.name)

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

        session["onboarding"] = {"org_id": org.id, "user_id": user.id}
        return redirect(url_for("onboarding.company_type"))

    return render_template("onboarding/start.html")


@onboarding_bp.route("/company-type", methods=["GET", "POST"])
def company_type():
    """Step 2: which vertical this org is -- drives the starter
    milestone preset (see PracticeType / services.practice_types)."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce

    if request.method == "POST":
        practice_type = PracticeType.query.get(request.form.get("practice_type_id"))
        if not practice_type:
            flash("Pick a company type to continue.", "error")
            return redirect(url_for("onboarding.company_type"))

        org.practice_type_id = practice_type.id
        db.session.flush()
        seed_org_milestones(org)
        db.session.commit()
        return redirect(url_for("onboarding.plan"))

    practice_types = PracticeType.query.order_by(PracticeType.name).all()
    return render_template("onboarding/company_type.html", practice_types=practice_types)


@onboarding_bp.route("/plan", methods=["GET", "POST"])
def plan():
    """Step 3: Free / Solo / Team. Team continues to the billing +
    invite steps; Free and Solo finish the wizard right here."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce

    if request.method == "POST":
        tier = request.form.get("tier")
        if tier not in WIZARD_TIERS:
            flash("Pick a plan to continue.", "error")
            return redirect(url_for("onboarding.plan"))

        org.tier = tier
        db.session.commit()

        if tier == "team":
            return redirect(url_for("onboarding.billing"))
        return _finish_signup(org, user)

    tiers = [
        {**current_app.config["PRICING_DISPLAY"][t], **current_app.config["TIER_LIMITS"][t], "tier_key": t}
        for t in WIZARD_TIERS
    ]
    preselect_tier = session.get("onboarding_preselect_tier", "free")
    return render_template("onboarding/plan.html", tiers=tiers, preselect_tier=preselect_tier)


@onboarding_bp.route("/billing", methods=["GET"])
def billing():
    """Step 4 (Team only): show the card-on-file step. The actual
    Stripe Checkout session is started by billing_start below."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    if org.tier != "team":
        return redirect(url_for("onboarding.plan"))

    return render_template(
        "onboarding/billing.html", org=org, stripe_configured=get_stripe() is not None,
    )


@onboarding_bp.route("/billing/start", methods=["POST"])
def billing_start():
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    if org.tier != "team":
        return redirect(url_for("onboarding.plan"))

    stripe, customer_id = org_billing.get_or_create_org_stripe_customer(org)
    if not stripe:
        flash("Card setup isn't available right now -- you can add one later from Settings → Billing.", "error")
        return redirect(url_for("onboarding.invites"))

    return_url = url_for("onboarding.billing_return", _external=True)
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="setup",
            customer=customer_id,
            payment_method_types=["card"],
            success_url=return_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("onboarding.billing", _external=True),
        )
    except Exception as e:
        current_app.logger.error("Stripe setup session creation failed (org %s): %s", org.id, e)
        flash("Couldn't start card setup — please try again.", "error")
        return redirect(url_for("onboarding.billing"))

    return redirect(checkout_session.url, code=303)


@onboarding_bp.route("/billing/return")
def billing_return():
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce

    stripe = get_stripe()
    session_id = request.args.get("session_id")
    if stripe and session_id:
        try:
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            saved = org_billing.save_org_payment_method_from_setup_intent(org, checkout_session.setup_intent)
            if saved:
                flash(f"{org.card_on_file_label()} saved as your card on file.", "success")
            else:
                flash("Couldn't confirm the card was saved — you can add one later from Settings → Billing.", "error")
        except Exception as e:
            current_app.logger.error("Stripe setup session retrieval failed (org %s): %s", org.id, e)
            flash("Couldn't confirm the card was saved — you can add one later from Settings → Billing.", "error")

    return redirect(url_for("onboarding.invites"))


@onboarding_bp.route("/team", methods=["GET", "POST"])
def invites():
    """Step 5 (Team only): invite up to 5 teammates by email, same
    pending-user + email-invite mechanism as team.new_member. Entirely
    optional -- an admin can always add seats later from Team."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    if org.tier != "team":
        return redirect(url_for("onboarding.plan"))

    if request.method == "POST":
        emails = [
            e.strip().lower()
            for e in request.form.getlist("invite_email")[:MAX_WIZARD_INVITES]
            if e.strip()
        ]

        sent, skipped = [], []
        for email in emails:
            if email == user.email or User.query.filter_by(email=email).first():
                skipped.append(email)
                continue

            member = User(
                org_id=org.id,
                email=email,
                role="agent",
                invited_by_user_id=user.id,
                status="pending",
                invite_token=secrets.token_urlsafe(32),
                invite_expires_at=datetime.utcnow() + timedelta(days=INVITE_EXPIRY_DAYS),
            )
            db.session.add(member)
            db.session.commit()

            invite_link = url_for("team.accept_invite", token=member.invite_token, _external=True)
            send_team_invite_email(member, invite_link, user.full_name)
            sent.append(email)

        if sent:
            flash(f"Invites sent to {', '.join(sent)}.", "success")
        if skipped:
            flash(f"Skipped (already registered): {', '.join(skipped)}.", "error")

        return _finish_signup(org, user)

    return render_template("onboarding/invites.html", org=org, max_invites=MAX_WIZARD_INVITES)


@onboarding_bp.route("/team/skip", methods=["POST"])
def invites_skip():
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    return _finish_signup(org, user)


def _finish_signup(org, user):
    """Common tail for every plan path: log the signup event, send the
    owner's verification email, and clear wizard session state."""
    record_org_event(org, "signup", None, org.tier)
    db.session.commit()

    delivered = _send_verification(user)
    session.pop("onboarding", None)
    session.pop("onboarding_preselect_tier", None)

    if not delivered:
        flash(
            "Account created, but we couldn't send the verification email. "
            "Try resending it below once things are set up.",
            "error",
        )
    return render_template("auth/check_email.html", email=user.email, purpose="verify")
