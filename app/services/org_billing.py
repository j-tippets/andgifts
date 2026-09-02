"""
Org-level Stripe Customer + subscription, used for the Team signup
wizard's billing step (see routes/onboarding.py) and to keep a
Team org's seat count in sync with what it's actually billed for.

Deliberately separate from services/payments.py, which is per-agent
and pays for gift orders -- this is the org's subscription
(Org.stripe_customer_id / Org.stripe_subscription_id /
Org.stripe_default_payment_method_id), not an agent's. Team is a real
per-seat subscription now (see config.STRIPE_PRICE_IDS["team"] and
config.TEAM_MIN_SEATS).

org.tier="team" can be granted by TWO independent paths that race
each other in production -- the checkout.session.completed webhook
(routes/orders.py, arrives async from Stripe) and the browser's own
return to /get-started/billing/return (routes/onboarding.py,
synchronous with the redirect) -- both go through
confirm_team_subscription_from_checkout_session below so whichever
lands first does the real work and the second is a harmless re-save
of the same details (see that function's docstring for the specific
idempotency guarantee).

confirm_team_subscription_from_checkout_session also (optionally)
shares the just-added card with the wizard's owner as their own
PaymentMethod for gift purchases, so Team signup only asks for a card
once -- see that function and _share_subscription_card_with_owner
below.
"""
from flask import current_app

from app.extensions import db
from app.services.stripe_client import get_stripe
from app.services.org_events import record_org_event


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


def confirm_team_subscription_from_checkout_session(org, session_id, owner=None):
    """The single place org.tier actually becomes "team". Called from
    BOTH the checkout.session.completed webhook (routes/orders.py --
    arrives async from Stripe, the durable source of truth) and the
    wizard's own browser return (routes/onboarding.billing_return --
    synchronous with the redirect, so the UI doesn't have to wait on
    the webhook to show "you're on Team"). Whichever of the two runs
    first does the real work; the second call re-validates and
    re-saves the same details rather than erroring or double-applying
    anything -- record_org_event only fires on an actual tier change,
    and _share_subscription_card_with_owner already no-ops on a
    retried PaymentMethod id.

    Independently re-fetches and validates the session/subscription
    from Stripe rather than trusting session_id alone: mode must be
    "subscription", the Checkout Session must be status="complete",
    the session's AND the subscription's org_id metadata must both
    match `org`, the subscription's price must be the configured Team
    price, and (once org.stripe_customer_id is known) the customer
    must match. Any mismatch is treated as "not confirmed" rather than
    raising -- this is what stands between an org and being handed
    Team for free (or another org's subscription) via a
    guessed/replayed/tampered session_id.

    Returns (confirmed: bool, shared_card_with_owner: bool).
    """
    stripe = get_stripe()
    if not stripe:
        return False, False

    try:
        checkout_session = stripe.checkout.Session.retrieve(
            session_id, expand=["subscription.default_payment_method"],
        )
    except Exception as e:
        current_app.logger.error(
            "Team checkout session retrieve failed (org %s, session %s): %s",
            org.id, session_id, e,
        )
        return False, False

    if checkout_session.get("mode") != "subscription":
        current_app.logger.warning(
            "Team checkout confirm rejected: wrong mode (org %s, session %s)", org.id, session_id,
        )
        return False, False

    if checkout_session.get("status") != "complete":
        return False, False

    session_org_id = checkout_session.get("client_reference_id") or (checkout_session.get("metadata") or {}).get("org_id")
    if session_org_id != org.id:
        current_app.logger.warning(
            "Team checkout confirm rejected: session org_id mismatch (org %s, session claims %s)",
            org.id, session_org_id,
        )
        return False, False

    subscription = checkout_session.subscription
    if not subscription:
        return False, False

    sub_org_id = (subscription.get("metadata") or {}).get("org_id")
    if sub_org_id != org.id:
        current_app.logger.warning(
            "Team checkout confirm rejected: subscription org_id mismatch (org %s, subscription claims %s)",
            org.id, sub_org_id,
        )
        return False, False

    expected_price_id = current_app.config["STRIPE_PRICE_IDS"].get("team")
    actual_price_id = subscription["items"]["data"][0]["price"]["id"]
    if not expected_price_id or actual_price_id != expected_price_id:
        current_app.logger.warning(
            "Team checkout confirm rejected: price mismatch (org %s, expected %s, got %s)",
            org.id, expected_price_id, actual_price_id,
        )
        return False, False

    if org.stripe_customer_id and checkout_session.customer != org.stripe_customer_id:
        current_app.logger.warning(
            "Team checkout confirm rejected: customer mismatch (org %s, expected %s, got %s)",
            org.id, org.stripe_customer_id, checkout_session.customer,
        )
        return False, False

    pm = subscription.default_payment_method
    card = (pm.card if pm else None) or {}

    old_tier = org.tier
    org.stripe_customer_id = checkout_session.customer
    org.stripe_subscription_id = subscription.id
    org.stripe_default_payment_method_id = pm.id if pm else None
    org.card_brand = card.get("brand")
    org.card_last4 = card.get("last4")
    org.card_exp_month = card.get("exp_month")
    org.card_exp_year = card.get("exp_year")
    org.tier = "team"
    if old_tier != "team":
        record_org_event(org, "upgrade", old_tier, "team")

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


def cancel_org_subscription(org):
    """Cancels org's live Stripe subscription, if it has one -- called
    from profile.delete_account BEFORE delete_org_completely touches
    any local rows, so a Stripe-side failure never results in an org
    (and its billing contact) being permanently deleted while Stripe
    keeps billing a subscription nobody can see or cancel anymore.

    Returns (ok, error_message_or_None):
    - (True, None): nothing to cancel (Stripe isn't configured, or
      this org never had a subscription) -- deletion should proceed.
    - (True, None): Stripe already reports the subscription as
      canceled -- makes this safe to call again on a retry (a browser
      refresh, a double form submit) without erroring on work that's
      already done.
    - (True, None): the cancel call succeeds.
    - (False, message): Stripe is configured and a subscription id is
      on file, but the cancel attempt itself failed for any other
      reason (network error, Stripe outage, etc). The caller MUST NOT
      proceed with deletion in this case -- fail closed. Leaving the
      org (temporarily) undeleted with a subscription still active is
      recoverable (retry, or cancel manually in the Stripe dashboard);
      deleting the org out from under an active subscription is not.
    """
    stripe = get_stripe()
    if not stripe or not org.stripe_subscription_id:
        return True, None

    try:
        subscription = stripe.Subscription.retrieve(org.stripe_subscription_id)
        if subscription.status == "canceled":
            return True, None
        stripe.Subscription.cancel(org.stripe_subscription_id)
        return True, None
    except Exception as e:
        current_app.logger.error(
            "Failed to cancel Stripe subscription for org %s before deletion: %s", org.id, e,
        )
        return False, str(e)
