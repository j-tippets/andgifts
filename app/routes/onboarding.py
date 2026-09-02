"""
Multi-step signup wizard: replaces the old single-page auth.register
form. Walks a new account through business basics, company type
(vertical), plan selection (Free / Solo / Team), and -- Team only --
up to 5 team-member invites followed by a subscription card on file,
before handing off to the existing email-verification flow.

Team's invites step runs BEFORE billing (not after): collecting the
team roster first lets the billing step size the Stripe subscription
quantity correctly from the start (owner + however many teammates
were entered, floored at TEAM_MIN_SEATS) instead of always charging
the 2-seat floor and patching the quantity up afterward. That step
deliberately only collects and validates emails, though -- it does
NOT create the pending User rows or send invite emails yet. Those are
deferred to _create_pending_invites, called once billing actually
resolves (checkout succeeds, or Stripe isn't configured at all), so
nobody is invited onto a team whose subscription signup was then
abandoned. See Org.onboarding_pending_invites for where the collected
emails live in the meantime.

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

The owner's verification email is sent immediately at the end of
Step 1 (not deferred to the end of the wizard), and Org.onboarding_step
tracks which step is next. That combination lets someone who abandons
the wizard click the emailed link later -- possibly in a different
browser/device than the one running the wizard -- and get dropped
back in at the right step instead of restarting or landing nowhere
useful (see routes/auth.verify_email, which reads onboarding_step and
re-seeds session["onboarding"] itself).
"""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from flask_login import current_user, login_user

from app.extensions import db, limiter
from app.models import User, Org, PracticeType
from app.services.email import send_team_invite_email
from app.services.org_events import record_org_event
from app.services.analytics import queue_event
from app.services.practice_types import seed_org_milestones
from app.services.stripe_client import get_stripe
from app.services.environment import is_production
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

        # Verification email now goes out here, right away, instead of
        # waiting for the wizard's final step (see _finish_signup) --
        # so someone who bails mid-wizard can still click the link and
        # get dropped back in (see routes/auth.verify_email) rather
        # than losing the account entirely.
        delivered = _send_verification(user)
        if not delivered:
            flash(
                "Account created, but we couldn't send the verification email. "
                "You can resend it later from Settings.",
                "error",
            )
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
        org.onboarding_step = "plan"
        db.session.flush()
        seed_org_milestones(org)
        db.session.commit()
        return redirect(url_for("onboarding.plan"))

    practice_types = PracticeType.query.order_by(PracticeType.name).all()
    return render_template("onboarding/company_type.html", practice_types=practice_types)


@onboarding_bp.route("/plan", methods=["GET", "POST"])
def plan():
    """Step 3: Free / Solo / Team. Team continues to the invites +
    billing steps (in that order -- see invites() for why); Free and
    Solo finish the wizard right here."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce

    if request.method == "POST":
        tier = request.form.get("tier")
        if tier not in WIZARD_TIERS:
            flash("Pick a plan to continue.", "error")
            return redirect(url_for("onboarding.plan"))

        if tier == "team":
            # Deliberately NOT org.tier = "team" here. Team is a paid
            # subscription -- org.tier is only allowed to become
            # "team" once Stripe actually confirms it (the
            # checkout.session.completed webhook, or the
            # dev-environment fallback in billing_start when Stripe
            # isn't configured at all). Selecting the plan just moves
            # the wizard forward; org.onboarding_step (not org.tier)
            # is what /team and /billing below gate on, so nothing
            # downstream ever treats "picked Team" as "is entitled to
            # Team" before payment resolves. See billing_return and
            # billing_start for where org.tier actually gets set.
            org.onboarding_step = "invites"
        else:
            org.tier = tier
            if tier == "starter" and org.trial_ends_at is None:
                # Only set once, ever -- an org that picks Solo, later
                # switches tiers, and comes back to Solo again should
                # NOT get a fresh 14 days (see trial_ends_at's comment
                # on Org).
                org.trial_ends_at = datetime.utcnow() + timedelta(days=current_app.config["TRIAL_DAYS"])
            org.onboarding_step = "done"
        db.session.commit()

        if tier == "team":
            return redirect(url_for("onboarding.invites"))
        return _finish_signup(org, user)

    tiers = [
        {**current_app.config["PRICING_DISPLAY"][t], **current_app.config["TIER_LIMITS"][t], "tier_key": t}
        for t in WIZARD_TIERS
    ]
    preselect_tier = session.get("onboarding_preselect_tier", "free")
    return render_template(
        "onboarding/plan.html",
        tiers=tiers,
        preselect_tier=preselect_tier,
        team_min_seats=current_app.config["TEAM_MIN_SEATS"],
        trial_days=current_app.config["TRIAL_DAYS"],
    )


def _team_seat_quantity(org):
    """How many seats the Team subscription should actually be for:
    the owner (always 1) plus however many teammates were collected on
    the invites step, floored at TEAM_MIN_SEATS. Computed fresh from
    org.pending_invite_emails() rather than trusting anything cached,
    since this is read both for the billing page's price preview and
    again at actual checkout-session creation."""
    from flask import current_app
    return max(current_app.config["TEAM_MIN_SEATS"], 1 + len(org.pending_invite_emails()))


def _create_pending_invites(org, inviter):
    """Turns whatever emails were collected on the invites step into
    real pending User rows + sent invite emails -- deferred until now
    (billing resolved: paid, or Stripe isn't configured) rather than
    done eagerly on the invites step itself, so nobody gets invited
    onto a team whose subscription checkout was then abandoned. See
    onboarding_bp's module docstring and Org.onboarding_pending_invites.
    Re-checks each email against existing Users at creation time (not
    just when it was first collected) in case one got registered
    elsewhere in the interim. Clears org.onboarding_pending_invites
    either way, so this is safe to call at most once per signup."""
    emails = org.pending_invite_emails()
    org.set_pending_invite_emails(None)
    if not emails:
        return [], []

    sent, skipped = [], []
    for email in emails:
        if email == inviter.email or User.query.filter_by(email=email).first():
            skipped.append(email)
            continue

        member = User(
            org_id=org.id,
            email=email,
            role="agent",
            invited_by_user_id=inviter.id,
            status="pending",
            invite_token=secrets.token_urlsafe(32),
            invite_expires_at=datetime.utcnow() + timedelta(days=INVITE_EXPIRY_DAYS),
        )
        db.session.add(member)
        db.session.commit()

        invite_link = url_for("team.accept_invite", token=member.invite_token, _external=True)
        send_team_invite_email(member, invite_link, inviter.full_name)
        sent.append(email)

    return sent, skipped


@onboarding_bp.route("/team", methods=["GET", "POST"])
def invites():
    """Step 4 (Team only): collect up to MAX_WIZARD_INVITES teammate
    emails. Deliberately does NOT create User rows or send invite
    emails yet -- that's deferred to _create_pending_invites, called
    once billing (the next step) actually resolves. This step just
    validates/dedupes the emails and stashes them on the org so the
    following billing step can size the Stripe subscription quantity
    correctly (owner + however many were collected here) instead of
    always billing the 2-seat floor and patching the quantity up
    afterward."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    # Gate on wizard progress, not org.tier -- org.tier isn't granted
    # "team" until payment resolves (see plan() above), so checking it
    # here would bounce a legitimate mid-wizard Team signup back to
    # Step 3.
    if org.onboarding_step != "invites":
        return redirect(url_for("onboarding.plan"))

    if request.method == "POST":
        seen = set()
        emails = []
        skipped = []
        for raw in request.form.getlist("invite_email")[:MAX_WIZARD_INVITES]:
            email = raw.strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            if email == user.email or User.query.filter_by(email=email).first():
                skipped.append(email)
                continue
            emails.append(email)

        org.set_pending_invite_emails(emails)
        org.onboarding_step = "billing"
        db.session.commit()

        if skipped:
            flash(f"Skipped (already registered): {', '.join(skipped)}.", "error")
        return redirect(url_for("onboarding.billing"))

    return render_template(
        "onboarding/invites.html",
        org=org,
        max_invites=MAX_WIZARD_INVITES,
        team_min_seats=current_app.config["TEAM_MIN_SEATS"],
        team_price=current_app.config["PRICING_DISPLAY"]["team"]["price_cents"] // 100,
    )


@onboarding_bp.route("/team/skip", methods=["POST"])
def invites_skip():
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    if org.onboarding_step != "invites":
        return redirect(url_for("onboarding.plan"))
    org.set_pending_invite_emails(None)
    org.onboarding_step = "billing"
    db.session.commit()
    return redirect(url_for("onboarding.billing"))


@onboarding_bp.route("/billing", methods=["GET"])
def billing():
    """Step 5 (Team only): show the card-on-file / subscribe step,
    with the seat count (and price) already reflecting whatever was
    collected on the invites step. The actual Stripe Checkout session
    is started by billing_start below."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    if org.onboarding_step != "billing":
        return redirect(url_for("onboarding.plan"))

    return render_template(
        "onboarding/billing.html",
        org=org,
        stripe_configured=get_stripe() is not None,
        seat_quantity=_team_seat_quantity(org),
        pending_invite_count=len(org.pending_invite_emails()),
        team_min_seats=current_app.config["TEAM_MIN_SEATS"],
        team_price=current_app.config["PRICING_DISPLAY"]["team"]["price_cents"] // 100,
        trial_days=current_app.config["TRIAL_DAYS"],
    )


@onboarding_bp.route("/billing/start", methods=["POST"])
def billing_start():
    """Starts the real Team subscription, quantity = owner + whatever
    teammates were collected on the invites step (floored at
    TEAM_MIN_SEATS) -- see _team_seat_quantity. Includes a
    TRIAL_DAYS-day trial (config.TRIAL_DAYS): Stripe puts the
    subscription in status=trialing and doesn't charge the card until
    the trial ends, so unlike Solo's trial (see Org.trial_ends_at)
    there's no local trial-tracking needed here at all -- Stripe's own
    subscription object already is the trial state, and the existing
    "webhook is truth" pattern picks up the eventual first charge the
    same way it picks up any other subscription event. org.tier is
    already "team" from the plan step; the checkout.session.completed
    webhook (routes/orders.py) is what actually records
    stripe_subscription_id as the system of record, same pattern as
    Starter/Pro self-serve checkout. If Stripe isn't configured at
    all, there's nothing to check out -- just create the collected
    invites and finish signup, same bar as the rest of this wizard for
    handling that."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce
    if org.onboarding_step != "billing":
        return redirect(url_for("onboarding.plan"))

    stripe, customer_id = org_billing.get_or_create_org_stripe_customer(org)
    price_id = current_app.config["STRIPE_PRICE_IDS"].get("team")
    if not stripe or not price_id:
        if is_production():
            # Stripe is a deployment misconfiguration in prod, not a
            # business-logic branch to gracefully route around --
            # finishing this signup any other way would either grant
            # Team for free (the bug this whole flow exists to avoid)
            # or silently downgrade to Free while still emailing real
            # invites to teammates as if they'd joined a paid Team.
            # Leave the org sitting at onboarding_step="billing" (they
            # can just retry) and surface the problem loudly.
            current_app.logger.error(
                "Team checkout unavailable in production (org %s): stripe=%s price_id=%s",
                org.id, bool(stripe), bool(price_id),
            )
            flash("Billing is temporarily unavailable -- please try again shortly, or contact support.", "error")
            return redirect(url_for("onboarding.billing"))

        # Local/dev convenience only: no Stripe keys configured at
        # all. Finish signup on Free (org.tier was never bumped to
        # "team" -- see plan() above) rather than pretending Team
        # billing succeeded.
        flash(
            "Billing setup isn't available in this environment -- continuing on Free. "
            "Add billing later from Settings → Billing.",
            "error",
        )
        sent, skipped = _create_pending_invites(org, user)
        if sent:
            flash(f"Invites sent to {', '.join(sent)}.", "success")
        if skipped:
            flash(f"Skipped (already registered): {', '.join(skipped)}.", "error")
        return _finish_signup(org, user)

    return_url = url_for("onboarding.billing_return", _external=True)
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": _team_seat_quantity(org)}],
            client_reference_id=org.id,
            metadata={"org_id": org.id, "tier": "team"},
            subscription_data={
                "trial_period_days": current_app.config["TRIAL_DAYS"],
                "metadata": {"org_id": org.id, "tier": "team"},
            },
            success_url=return_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("onboarding.billing", _external=True),
        )
    except Exception as e:
        current_app.logger.error("Stripe subscription checkout creation failed (org %s): %s", org.id, e)
        flash("Couldn't start checkout — please try again.", "error")
        return redirect(url_for("onboarding.billing"))

    return redirect(checkout_session.url, code=303)


@onboarding_bp.route("/billing/return")
def billing_return():
    """Stripe sent us back here with a session id -- independently
    verify the checkout session actually belongs to this org and was
    for a completed Team subscription (see
    org_billing.confirm_team_subscription_from_checkout_session), and
    only THEN grant org.tier="team". This is a race with the
    checkout.session.completed webhook, not a replacement for it --
    whichever confirms first wins, the other is a no-op re-save (see
    that function's docstring).

    If confirmation fails or never runs (Stripe not configured,
    missing/tampered session_id, a real Stripe-side hiccup), org.tier
    is simply left as whatever it already was -- never assumed to be
    "team" -- and the wizard still finishes so the person isn't
    stranded, just on whatever tier they're actually entitled to."""
    org, user, bounce = _require_wizard()
    if bounce:
        return bounce

    stripe = get_stripe()
    session_id = request.args.get("session_id")
    confirmed = False
    if stripe and session_id:
        try:
            confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(
                org, session_id, owner=user,
            )
        except Exception as e:
            current_app.logger.error("Stripe checkout session retrieval failed (org %s): %s", org.id, e)

        if confirmed:
            if shared:
                flash(
                    f"{org.card_on_file_label()} saved -- you're set up on Team, "
                    f"and this card is now on file for your own gift purchases too.",
                    "success",
                )
            else:
                flash(f"{org.card_on_file_label()} saved -- you're set up on Team.", "success")

    if not confirmed:
        flash("Couldn't confirm your subscription — check Settings → Billing once you're in.", "error")

    if confirmed:
        # Only invite teammates onto a subscription that's actually
        # confirmed -- see this module's docstring on why that's
        # deferred until billing resolves. If confirmation failed here,
        # the collected emails stay on org.onboarding_pending_invites;
        # if the webhook independently confirms moments later it won't
        # replay this invite step (onboarding is already marked done),
        # so a failed browser-return currently means those invites need
        # sending manually from Team → Invite once the org owner
        # verifies billing in Settings. Worth a follow-up if this proves
        # to happen often in practice.
        sent, skipped = _create_pending_invites(org, user)
        if sent:
            flash(f"Invites sent to {', '.join(sent)}.", "success")
        if skipped:
            flash(f"Skipped (already registered): {', '.join(skipped)}.", "error")

    return _finish_signup(org, user)



def _finish_signup(org, user):
    """Common tail for every plan path: log the signup event, mark the
    wizard done, and clear wizard session state. The verification
    email already went out at the end of Step 1 (see start()), not
    here -- if the person verified mid-wizard already (e.g. clicked
    the link from another tab/device), just take them on into the
    dashboard instead of showing a "check your email" page for a step
    that's already done."""
    org.onboarding_step = "done"
    record_org_event(org, "signup", None, org.tier)
    queue_event("sign_up", method="email", user_role=user.role)
    queue_event("org_created", org_tier=org.tier)
    db.session.commit()

    session.pop("onboarding", None)
    session.pop("onboarding_preselect_tier", None)

    if user.email_verified:
        login_user(user)
        flash(f"Welcome to {org.name}!", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/check_email.html", email=user.email, purpose="verify")
