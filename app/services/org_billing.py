"""
Org-level Stripe Customer + saved card, used ONLY for the Team
signup wizard's "put a card on file" step (see routes/onboarding.py).

Deliberately separate from services/payments.py, which is per-agent
and pays for gift orders -- this is the org's subscription card
(Org.stripe_customer_id / Org.stripe_default_payment_method_id), not
an agent's. Team is custom-priced (no STRIPE_PRICE_IDS entry), so
this never creates a live subscription -- it saves a card via a
SetupIntent the same way settings.add_payment_method does for
agents, just scoped to the org instead of the user.
"""
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


def save_org_payment_method_from_setup_intent(org, setup_intent_id):
    """Called after the wizard's Setup Checkout session completes (see
    routes/onboarding.billing_return). Retrieves the resulting
    PaymentMethod from Stripe, sets it as Stripe's default for the
    customer's future invoices, and snapshots brand/last4/exp onto the
    org row for display. Idempotent: re-hitting the return URL just
    re-saves the same details rather than erroring."""
    stripe = get_stripe()
    if not stripe:
        return False

    setup_intent = stripe.SetupIntent.retrieve(setup_intent_id)
    pm_id = setup_intent.payment_method
    if not pm_id:
        return False

    stripe_pm = stripe.PaymentMethod.retrieve(pm_id)
    card = stripe_pm.card or {}

    stripe.Customer.modify(
        org.stripe_customer_id,
        invoice_settings={"default_payment_method": pm_id},
    )

    org.stripe_default_payment_method_id = pm_id
    org.card_brand = card.get("brand")
    org.card_last4 = card.get("last4")
    org.card_exp_month = card.get("exp_month")
    org.card_exp_year = card.get("exp_year")
    db.session.commit()
    return True
