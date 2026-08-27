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

save_org_subscription_from_checkout_session also (optionally) shares
the just-added card with the wizard's owner as their own PaymentMethod
for gift purchases, so Team signup only asks for a card once -- see
that function and _share_subscription_card_with_owner below.
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


def save_org_subscription_from_checkout_session(org, session_id, owner=None):
    """Called after the wizard's Team subscription Checkout session
    completes (see routes/onboarding.billing_return). Mirrors the
    subscription id + card display details onto the org row so the
    wizard's own pages can show "card on file" immediately, without
    waiting on the checkout.session.completed webhook (which remains
    the only thing allowed to actually set org.tier/stripe_subscription_id
    as the system of record -- see routes/orders.py). Idempotent:
    re-hitting the return URL just re-saves the same details.

    If `owner` is given (the person running the wizard) and doesn't
    already have their own separate gifting Stripe Customer/cards,
    this also reuses the just-added card as their saved PaymentMethod
    for gift purchases -- see _share_subscription_card_with_owner --
    so they aren't asked to enter the same card twice during
    onboarding. Returns (saved, shared_with_owner)."""
    stripe = get_stripe()
    if not stripe:
        return False, False

    checkout_session = stripe.checkout.Session.retrieve(
        session_id, expand=["subscription.default_payment_method"],
    )
    subscription = checkout_session.subscription
    if not subscription:
        return False, False

    pm = subscription.default_payment_method
    card = (pm.card if pm else None) or {}

    org.stripe_customer_id = checkout_session.customer
    org.stripe_subscription_id = subscription.id
    org.stripe_default_payment_method_id = pm.id if pm else None
    org.card_brand = card.get("brand")
    org.card_last4 = card.get("last4")
    org.card_exp_month = card.get("exp_month")
    org.card_exp_year = card.get("exp_year")

    shared = False
    if owner is not None and pm is not None:
        shared = _share_subscription_card_with_owner(owner, org, pm, card)

    db.session.commit()
    return True, shared


def _share_subscription_card_with_owner(owner, org, pm, card):
    """Reuses the card just added for the Team subscription as the
    owner's own saved PaymentMethod for gift purchases too (see
    services/payments.py + PaymentMethod), instead of asking them to
    enter the same card again in Settings. Safe because it's the same
    underlying Stripe Customer -- the subscription and the owner's
    gift charges (services.payments.charge_saved_card) end up hitting
    the same Customer/PaymentMethod pair on Stripe's side, which
    Stripe has no problem with.

    Only kicks in if the owner doesn't already have their own separate
    gifting Stripe Customer or saved cards -- if they do (e.g. they'd
    already added a personal card before running Team signup), leave
    that alone rather than silently repointing an existing setup at a
    different Stripe Customer. This is also why removing this card
    from Settings is blocked while it's still the subscription's
    default -- see services/payments.remove_payment_method."""
    from app.models import PaymentMethod
    if owner.stripe_customer_id or owner.payment_methods:
        return False
    if PaymentMethod.query.filter_by(stripe_payment_method_id=pm.id).first():
        return False  # already recorded, e.g. a retried return hit

    owner.stripe_customer_id = org.stripe_customer_id
    db.session.add(PaymentMethod(
        user_id=owner.id,
        stripe_payment_method_id=pm.id,
        card_brand=card.get("brand"),
        card_last4=card.get("last4"),
        card_exp_month=card.get("exp_month"),
        card_exp_year=card.get("exp_year"),
        is_default=True,
    ))
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
