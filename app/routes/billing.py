"""
Self-serve subscription billing: checkout for upgrading off Free, and
Stripe's hosted Billing Portal for everything else (downgrade, cancel,
update payment method, view invoices) -- see billing_portal() for why
that's Stripe-hosted rather than custom-built here.

org.tier is the single source of truth the rest of the app reads (see
Org.limit_for / TIER_LIMITS); this module's only job is keeping it in
sync with what's actually true in Stripe. The webhook handler
(extends the existing one in routes/orders.py) is the ONLY thing
allowed to change org.tier as a result of a payment event -- same
"webhook is truth, browser redirect is just a courtesy" pattern
orders.py already uses for one-off gift checkout.
"""
from flask import Blueprint, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.decorators import admin_required
from app.services.stripe_client import get_stripe

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")

SELF_SERVE_TIERS = ("starter", "pro", "team")


@billing_bp.route("/checkout/<tier>", methods=["POST"])
@admin_required
def checkout(tier):
    """Starts a Stripe Checkout session to move this org onto a paid
    tier. Admin-only -- this is an org-level financial commitment, not
    something any agent should be able to trigger."""
    org = current_user.org

    if tier not in SELF_SERVE_TIERS:
        flash("That plan isn't available for self-serve checkout -- contact us for Team pricing.", "error")
        return redirect(url_for("pages.pricing"))

    if org.billing_type != "card":
        # net30/net60 orgs are on a manually-invoiced arrangement (see
        # Org.billing_type) -- self-serve card checkout would create a
        # second, conflicting billing relationship for the same org.
        flash("Your account is on invoiced billing -- contact us to change plans.", "error")
        return redirect(url_for("pages.pricing"))

    if org.stripe_subscription_id:
        # Already has an active subscription -- Checkout Sessions create
        # a NEW subscription, they don't modify an existing one, so
        # running this again would leave the org double-billed on two
        # separate subscriptions. Plan switches for an existing
        # subscriber go through the Billing Portal instead (see
        # portal() below), which modifies the existing subscription
        # in place (with correct proration) rather than stacking a
        # second one.
        flash("You already have an active plan -- use \u201cManage billing\u201d to switch plans.", "error")
        return redirect(url_for("settings.billing"))

    price_id = current_app.config["STRIPE_PRICE_IDS"].get(tier)
    if not price_id:
        current_app.logger.error("No STRIPE_PRICE_IDS entry configured for tier=%s", tier)
        flash("Checkout isn't set up for that plan yet -- try again shortly.", "error")
        return redirect(url_for("pages.pricing"))

    stripe = get_stripe()
    if not stripe:
        flash("Billing isn't available right now -- try again shortly.", "error")
        return redirect(url_for("pages.pricing"))

    # Pro and Team are priced per-seat; Team additionally has a 2-seat
    # floor (see config.TEAM_MIN_SEATS) even if the org itself only has
    # 1 active user right now. Starter is flat (always quantity 1,
    # even though it's a single-seat tier anyway -- keeps the Checkout
    # Session creation uniform).
    if tier == "pro":
        quantity = max(1, org.seat_count())
    elif tier == "team":
        quantity = max(current_app.config["TEAM_MIN_SEATS"], org.seat_count())
    else:
        quantity = 1

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": quantity}],
            customer=org.stripe_customer_id,  # None is fine -- Stripe creates one
            customer_email=None if org.stripe_customer_id else current_user.email,
            client_reference_id=org.id,
            metadata={"org_id": org.id, "tier": tier},
            subscription_data={"metadata": {"org_id": org.id, "tier": tier}},
            success_url=url_for("billing.checkout_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("billing.checkout_cancelled", _external=True),
        )
    except Exception as e:
        current_app.logger.error("Stripe checkout session creation failed for org %s: %s", org.id, e)
        flash("Something went wrong starting checkout. Please try again.", "error")
        return redirect(url_for("pages.pricing"))

    return redirect(session.url, code=303)


@billing_bp.route("/checkout/success")
@login_required
def checkout_success():
    """Courtesy landing page only -- org.tier is NOT changed here. The
    checkout.session.completed webhook (Stripe-signature-verified) is
    the only thing allowed to actually flip the tier, since a browser
    hitting this URL proves nothing on its own (see the module
    docstring, and orders.order_success for the identical pattern)."""
    flash("Payment received -- your plan will update within a few seconds.", "success")
    return redirect(url_for("settings.billing"))


@billing_bp.route("/checkout/cancelled")
@login_required
def checkout_cancelled():
    flash("Checkout cancelled -- you're still on your current plan.", "error")
    return redirect(url_for("settings.billing"))


@billing_bp.route("/portal")
@login_required
@admin_required
def portal():
    """Hands off to Stripe's hosted Billing Portal for anything that
    isn't 'start a new subscription': downgrade, cancel, swap between
    plans, update the card on file, view past invoices. Deliberately
    NOT custom-built -- Stripe's portal already handles proration,
    payment-method re-auth (3DS etc.), and dunning/failed-payment
    retry UX correctly, and keeping downgrade/cancel logic OUT of this
    codebase means there's no custom code here that can get those
    subtly wrong.

    Requires Jeremiah to have completed the one-time Stripe Dashboard
    setup for this (Settings -> Billing -> Customer portal): turn on
    'Customers can switch plans' and list starter+pro as the allowed
    products or the portal will only offer cancel, not upgrade/
    downgrade between the two.

    Only reachable once this org actually has a Stripe customer (i.e.
    has been through checkout at least once) -- nothing to manage in
    the portal before that."""
    org = current_user.org
    if not org.stripe_customer_id:
        flash("You're on the Free plan -- pick a paid plan first to manage billing.", "error")
        return redirect(url_for("pages.pricing"))

    stripe = get_stripe()
    if not stripe:
        flash("Billing isn't available right now -- try again shortly.", "error")
        return redirect(url_for("settings.billing"))

    try:
        session = stripe.billing_portal.Session.create(
            customer=org.stripe_customer_id,
            return_url=url_for("settings.billing", _external=True),
        )
    except Exception as e:
        current_app.logger.error("Stripe billing portal session creation failed for org %s: %s", org.id, e)
        flash("Something went wrong opening billing management. Please try again.", "error")
        return redirect(url_for("settings.billing"))

    return redirect(session.url, code=303)
