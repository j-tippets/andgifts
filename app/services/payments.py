"""
Saved-card infrastructure for gift purchases -- a per-agent Stripe
Customer + PaymentMethod list (see User.stripe_customer_id and the
PaymentMethod model), separate from Org.stripe_customer_id
(subscription billing). Shared by the manual one-off order flow and
automated flow-triggered approvals -- both ultimately call
charge_saved_card() the same way.
"""
import logging
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import ActionLog, PaymentMethod
from app.services.stripe_client import get_stripe

logger = logging.getLogger(__name__)


def get_or_create_stripe_customer(user):
    """Returns (stripe, customer_id), or (None, None) if Stripe isn't
    configured. Creates the Customer on Stripe lazily, on this agent's
    first card add -- not at signup, so orgs that never use gifting
    don't accumulate unused Stripe Customers."""
    stripe = get_stripe()
    if not stripe:
        return None, None
    if user.stripe_customer_id:
        return stripe, user.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email,
        name=user.full_name,
        metadata={"user_id": user.id, "org_id": user.org_id},
    )
    user.stripe_customer_id = customer.id
    db.session.commit()
    return stripe, customer.id


def save_payment_method_from_setup_intent(user, setup_intent_id):
    """Called after a Setup Checkout session completes (see
    routes/settings.add_payment_method_return) -- retrieves the
    resulting PaymentMethod from Stripe and saves a local record.
    Auto-sets it default if this is the agent's first card (see
    PaymentMethod's docstring for why exactly one default must always
    exist once any card does). Idempotent: re-hitting the return URL
    (a refresh, a double-back) just returns the already-saved row
    rather than erroring or duplicating."""
    stripe = get_stripe()
    if not stripe:
        return None

    setup_intent = stripe.SetupIntent.retrieve(setup_intent_id)
    pm_id = setup_intent.payment_method
    if not pm_id:
        return None

    existing = PaymentMethod.query.filter_by(stripe_payment_method_id=pm_id).first()
    if existing:
        return existing

    stripe_pm = stripe.PaymentMethod.retrieve(pm_id)
    card = stripe_pm.card or {}
    is_first_card = not user.payment_methods

    payment_method = PaymentMethod(
        user_id=user.id,
        stripe_payment_method_id=pm_id,
        card_brand=card.get("brand"),
        card_last4=card.get("last4"),
        card_exp_month=card.get("exp_month"),
        card_exp_year=card.get("exp_year"),
        is_default=is_first_card,
    )
    db.session.add(payment_method)
    db.session.commit()
    return payment_method


def set_default_payment_method(user, payment_method_id):
    """Unsets every other card's default flag -- exactly one (or zero,
    if the agent has none at all) can be default at a time."""
    for pm in user.payment_methods:
        pm.is_default = (pm.id == payment_method_id)
    db.session.commit()


def remove_payment_method(user, payment_method_id):
    """Detaches from Stripe and deletes the local row. If the removed
    card was the default and other cards remain, promotes the oldest
    remaining one -- automated approvals should never silently end up
    with no default while the agent still has a usable card.

    Refuses to remove a card that's currently the org's Team
    subscription default payment method (see
    org_billing.share_subscription_card_with_owner) -- detaching it
    from Stripe would break the subscription's automatic renewal, not
    just this agent's own gift charges. That card can only be changed
    from Settings → Billing.

    Returns (True, None) on success, or (False, reason) where reason
    is "subscription_card" for that specific refusal, or "not_found"."""
    payment_method = PaymentMethod.query.filter_by(id=payment_method_id, user_id=user.id).first()
    if not payment_method:
        return False, "not_found"

    if user.org and user.org.stripe_default_payment_method_id == payment_method.stripe_payment_method_id:
        return False, "subscription_card"

    stripe = get_stripe()
    if stripe:
        try:
            stripe.PaymentMethod.detach(payment_method.stripe_payment_method_id)
        except Exception:
            pass  # already detached/gone on Stripe's side -- still remove our record

    was_default = payment_method.is_default
    db.session.delete(payment_method)
    db.session.flush()

    if was_default:
        remaining = (
            PaymentMethod.query.filter_by(user_id=user.id)
            .order_by(PaymentMethod.created_at).first()
        )
        if remaining:
            remaining.is_default = True

    db.session.commit()
    return True, None


def _spend_guard_error(user, amount_cents):
    """Hard ceilings on what a single charge, and one agent's charges in
    a rolling 24 hours, are allowed to total. Returns an agent-facing
    error string if this charge should be refused, else None.

    Deliberately blunt. This is not the per-agent monthly budget feature
    a brokerage will eventually want -- it's the backstop that stands
    between a misconfigured flow and a five-figure card statement.
    Right now nothing does: a flow with a bad rule can approve
    repeatedly, and each approval charges a real card with no aggregate
    limit anywhere in the system.

    Both limits are needed. A per-charge ceiling alone doesn't stop a
    runaway flow, which charges many normal-sized amounts rather than
    one huge one; a daily total alone doesn't stop a single absurd
    charge from a bad price field.

    Thresholds are set well above any legitimate use (gift tiers top out
    at $500) and are env-tunable, so hitting one means something is
    wrong rather than that a customer is busy. Enforced here because
    charge_saved_card is the one function every charge in the app --
    manual order and automated approval alike -- passes through.
    """
    max_single = current_app.config.get("MAX_SINGLE_CHARGE_CENTS")
    if max_single and amount_cents > max_single:
        return (
            f"This charge (${amount_cents / 100:,.2f}) is above the "
            f"${max_single / 100:,.2f} single-charge limit. Contact support if "
            "this is a legitimate order."
        )

    max_daily = current_app.config.get("MAX_AGENT_DAILY_CHARGE_CENTS")
    if not max_daily:
        return None

    # ActionLog is the shared spend ledger -- both the manual order flow
    # and automated approvals write a row with cost_cents after a
    # successful charge, so summing it counts real money actually taken.
    # The current charge isn't in it yet, which is why it's added below
    # rather than compared on its own.
    since = datetime.utcnow() - timedelta(hours=24)
    spent_cents = (
        db.session.query(func.coalesce(func.sum(ActionLog.cost_cents), 0))
        .filter(
            ActionLog.approved_by_user_id == user.id,
            ActionLog.sent_at >= since,
        )
        .scalar()
    ) or 0

    if spent_cents + amount_cents > max_daily:
        return (
            f"This would put {user.full_name} over the ${max_daily / 100:,.2f} "
            "daily spend limit. Contact support if this is expected."
        )
    return None


def charge_saved_card(user, amount_cents, description, metadata=None, idempotency_key=None,
                      payment_method=None):
    """Charges `user`'s default saved card off-session -- there's no
    live card form in either caller (the in-app order confirm screen
    is just a "Charge $X" button, and an automated flow approval has
    no customer present at all), so this is always an off-session
    reuse of an already-saved, already-verified payment method rather
    than a fresh on-session confirmation.

    idempotency_key, when provided, is passed straight through to
    Stripe's PaymentIntent.create -- Stripe uses it to recognize a
    retried request (a network timeout on our end where Stripe's
    server actually processed the original call, a caller re-invoking
    this after a crash before it recorded success, etc.) and return
    the ORIGINAL PaymentIntent instead of creating a second charge.
    Callers billing a specific, already-identified thing (a
    SuggestedAction approval) should pass a deterministic key derived
    from that thing's id so a retry of the exact same intended charge
    reuses it; a fresh ad-hoc charge (no natural stable identity yet)
    can leave this None.

    Returns (success, payment_intent_id_or_None, error_message_or_None).
    A decline (stripe.error.CardError) or a card that unexpectedly
    needs additional authentication for this specific charge both come
    back as a plain failure with Stripe's own message -- callers decide
    what "blocked" means for their own flow (see routes/dashboard.approve_action
    and routes/orders, both of which stay pending/unconfirmed on failure
    rather than silently proceeding)."""
    # `payment_method` is the card the caller explicitly wants charged.
    # The in-app order flow has one: the agent picks a card at
    # routes/orders.choose_payment and it is stored on the order. That
    # selection used to be written and never read -- this function
    # always charged user.default_payment_method, so an agent who chose
    # their business card and had a personal card set as default was
    # charged the personal one, with the confirmation screen showing the
    # card they picked. Silent, and wrong in the direction that is
    # hardest to notice.
    #
    # Automated flow approvals genuinely have no chosen card (no
    # customer is present), so they pass nothing and the default is
    # still correct for them.
    charge_pm = payment_method or user.default_payment_method
    if not charge_pm:
        return False, None, "No card on file -- add one in Settings first."
    if charge_pm.user_id != user.id:
        # Defensive: a card belonging to someone else must never be
        # chargeable, whatever the caller passed.
        return False, None, "That payment method isn't available on this account."

    # Before Stripe, so a refused charge costs nothing and leaves no
    # PaymentIntent behind.
    spend_error = _spend_guard_error(user, amount_cents)
    if spend_error:
        _report_spend_block(user, amount_cents, spend_error)
        return False, None, spend_error

    # Deliberately after the checks above: a card belonging to another
    # user is a caller bug and should be reported as such whether or not
    # Stripe happens to be configured in this environment.
    stripe = get_stripe()
    if not stripe:
        return False, None, "Payments aren't configured yet."

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="usd",
            customer=user.stripe_customer_id,
            payment_method=charge_pm.stripe_payment_method_id,
            off_session=True,
            confirm=True,
            description=description,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
        )
        return True, intent.id, None
    except stripe.error.CardError as e:
        return False, None, (e.user_message or "Card was declined.")
    except Exception as e:
        return False, None, str(e)


def _report_spend_block(user, amount_cents, reason):
    """A tripped ceiling is never routine -- it means either a
    misconfigured flow or a genuine customer being wrongly blocked, and
    both need a human. Logged and sent to Sentry; never raises."""
    logger.warning(
        "Spend guard blocked a charge: user=%s org=%s amount_cents=%s reason=%s",
        user.id, user.org_id, amount_cents, reason,
    )
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("user_id", user.id)
            scope.set_tag("org_id", user.org_id)
            scope.set_extra("amount_cents", amount_cents)
            scope.set_level("warning")
            sentry_sdk.capture_message(f"Spend guard blocked a charge: {reason}")
    except Exception:
        pass
