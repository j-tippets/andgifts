"""
Org-level Stripe Customer + subscription, used for the Team signup
wizard's billing step (see routes/onboarding.py) and to keep a
Team org's seat count in sync with what it's actually billed for.

Deliberately separate from services/payments.py, which is per-agent
and pays for gift orders -- this is the org's subscription
(Org.stripe_customer_id / Org.stripe_subscription_id /
Org.stripe_default_payment_method_id), not an agent's. Team is a real
per-seat subscription now (see config.STRIPE_PRICE_IDS["team"] and
config.TEAM_MIN_SEATS), same "webhook is truth" pattern as the
Starter/Pro self-serve checkout in routes/billing.py -- this module
only creates the Checkout Session and mirrors card-display details
onto the org row for the wizard's own UI; org.tier itself is only
ever changed by the checkout.session.completed webhook.
"""
from flask import current_app

from app.extensions import db
from app.services.stripe_client import get_stripe


def get_or_create_org_stripe_customer(org):
    """Returns (stripe, customer_id), or (None, None) if Stripe isn't
    configured. Reuses org.stripe_customer_id if a paid-tier checkout
    (routes/billing.py) already created one for this org."""
    stripe = get_stripe()
    if not stripe:
        return None, None
    if org.stripe_customer_id:
        return stripe, org.stripe_customer_id
    customer = stripe.Customer.create(
        name=org.name,
        metadata={"org_id": org.id},
    )
    org.stripe_customer_id = customer.id
    db.session.commit()
    return stripe, customer.id


def save_org_subscription_from_checkout_session(org, session_id):
    """Called after the wizard's Team subscription Checkout session
    completes (see routes/onboarding.billing_return). Mirrors the
    subscription id + card display details onto the org row so the
    wizard's own pages can show "card on file" immediately, without
    waiting on the checkout.session.completed webhook (which remains
    the only thing allowed to actually set org.tier/stripe_subscription_id
    as the system of record -- see routes/orders.py). Idempotent:
    re-hitting the return URL just re-saves the same details."""
    stripe = get_stripe()
    if not stripe:
        return False

    checkout_session = stripe.checkout.Session.retrieve(
        session_id, expand=["subscription.default_payment_method"],
    )
    subscription = checkout_session.subscription
    if not subscription:
        return False

    pm = subscription.default_payment_method
    card = (pm.card if pm else None) or {}

    org.stripe_customer_id = checkout_session.customer
    org.stripe_subscription_id = subscription.id
    org.stripe_default_payment_method_id = pm.id if pm else None
    org.card_brand = card.get("brand")
    org.card_last4 = card.get("last4")
    org.card_exp_month = card.get("exp_month")
    org.card_exp_year = card.get("exp_year")
    db.session.commit()
    return True


def sync_team_subscription_quantity(org):
    """Keeps a Team org's Stripe subscription quantity matched to its
    actual seat count, with the 2-seat floor (config.TEAM_MIN_SEATS)
    as a lower bound -- so inviting teammates during onboarding (or
    later, from Team) bills for the seats actually in use once that
    exceeds what was already charged at signup. Never lowers quantity
    on its own; removing a seat doesn't get a mid-cycle credit here.
    No-ops if there's no live subscription yet (Stripe not configured,
    or the org skipped the billing step)."""
    stripe = get_stripe()
    if not stripe or not org.stripe_subscription_id:
        return False

    desired_qty = max(current_app.config["TEAM_MIN_SEATS"], org.seat_count())
    try:
        subscription = stripe.Subscription.retrieve(org.stripe_subscription_id)
        item = subscription["items"]["data"][0]
        if item["quantity"] < desired_qty:
            stripe.SubscriptionItem.modify(item["id"], quantity=desired_qty)
        return True
    except Exception as e:
        current_app.logger.error(
            "Failed to sync Team subscription quantity for org %s: %s", org.id, e,
        )
        return False
