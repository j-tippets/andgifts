"""
Saved-card infrastructure for gift purchases -- a per-agent Stripe
Customer + PaymentMethod list (see User.stripe_customer_id and the
PaymentMethod model), separate from Org.stripe_customer_id
(subscription billing). Shared by the manual one-off order flow and
automated flow-triggered approvals -- both ultimately call
charge_saved_card() the same way.
"""
from app.extensions import db
from app.models import PaymentMethod
from app.services.stripe_client import get_stripe


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


def charge_saved_card(user, amount_cents, description, metadata=None):
    """Charges `user`'s default saved card off-session -- there's no
    live card form in either caller (the in-app order confirm screen
    is just a "Charge $X" button, and an automated flow approval has
    no customer present at all), so this is always an off-session
    reuse of an already-saved, already-verified payment method rather
    than a fresh on-session confirmation.

    Returns (success, payment_intent_id_or_None, error_message_or_None).
    A decline (stripe.error.CardError) or a card that unexpectedly
    needs additional authentication for this specific charge both come
    back as a plain failure with Stripe's own message -- callers decide
    what "blocked" means for their own flow (see routes/dashboard.approve_action
    and routes/orders, both of which stay pending/unconfirmed on failure
    rather than silently proceeding)."""
    stripe = get_stripe()
    if not stripe:
        return False, None, "Payments aren't configured yet."

    default_pm = user.default_payment_method
    if not default_pm:
        return False, None, "No card on file -- add one in Settings first."

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="usd",
            customer=user.stripe_customer_id,
            payment_method=default_pm.stripe_payment_method_id,
            off_session=True,
            confirm=True,
            description=description,
            metadata=metadata or {},
        )
        return True, intent.id, None
    except stripe.error.CardError as e:
        return False, None, (e.user_message or "Card was declined.")
    except Exception as e:
        return False, None, str(e)
