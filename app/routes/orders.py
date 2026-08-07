from datetime import datetime

from flask import Blueprint, render_template, request, current_app, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Order, ActionLog, ContactAuditLog, Org
from app.services.stripe_client import get_stripe
from app.services.email import send_order_confirmation

orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/orders/<order_id>/success")
@login_required
def order_success(order_id):
    """Landing page after Stripe Checkout. This is a courtesy screen only
    -- the order isn't marked paid here. The checkout.session.completed
    webhook (verified via Stripe's signature) is the only thing allowed
    to flip status to 'paid', since a browser hitting this URL proves
    nothing on its own."""
    order = Order.query.filter_by(id=order_id, org_id=current_user.org_id).first_or_404()
    return render_template("orders/success.html", order=order)


@orders_bp.route("/orders/<order_id>/cancelled")
@login_required
def order_cancelled(order_id):
    order = Order.query.filter_by(id=order_id, org_id=current_user.org_id).first_or_404()
    if order.status == "pending":
        order.status = "cancelled"
        db.session.commit()
    return render_template("orders/cancelled.html", order=order)


def _tier_for_price_id(price_id):
    """Reverse lookup for the webhook handlers below -- Stripe tells us
    which price a subscription is on, we need to know which tier that
    maps to. Returns None for an unrecognized price (shouldn't happen
    for anything created through our own checkout, but a manually
    created subscription in the Stripe dashboard could reference a
    price we don't know about)."""
    price_ids = current_app.config["STRIPE_PRICE_IDS"]
    for tier, pid in price_ids.items():
        if pid and pid == price_id:
            return tier
    return None


@orders_bp.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    stripe = get_stripe()
    if not stripe:
        abort(503)

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")

    if not webhook_secret:
        current_app.logger.error("STRIPE_WEBHOOK_SECRET not configured; rejecting webhook.")
        abort(503)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        current_app.logger.error("Stripe webhook signature verification failed: %s", e)
        abort(400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        if session.get("mode") == "subscription":
            # A subscription checkout (see billing.checkout) -- separate
            # from the one-off gift-order checkout below, which shares
            # this same webhook endpoint since Stripe only lets an
            # account register one endpoint per event type.
            org_id = session.get("client_reference_id") or (session.get("metadata") or {}).get("org_id")
            tier = (session.get("metadata") or {}).get("tier")
            org = Org.query.get(org_id) if org_id else None

            if not org or not tier:
                current_app.logger.error(
                    "Subscription checkout.session.completed missing org_id/tier (org_id=%s tier=%s session=%s)",
                    org_id, tier, session.get("id"),
                )
            else:
                org.stripe_customer_id = session.get("customer")
                org.stripe_subscription_id = session.get("subscription")
                org.tier = tier
                db.session.commit()
            return ("", 200)

        # mode == "payment": the existing one-off gift-order flow.
        order = Order.query.filter_by(stripe_checkout_session_id=session["id"]).first()

        if order and order.status == "pending":
            order.status = "paid"
            order.paid_at = datetime.utcnow()
            order.stripe_payment_intent_id = session.get("payment_intent")

            shipping_details = session.get("shipping_details")
            if shipping_details:
                address = shipping_details.get("address") or {}
                address_line = ", ".join(filter(None, [
                    address.get("line1"),
                    address.get("line2"),
                    address.get("city"),
                    address.get("state"),
                    address.get("postal_code"),
                ]))
                name = shipping_details.get("name")
                order.shipping_address_snapshot = f"{name}\n{address_line}" if name else address_line

            db.session.add(ActionLog(
                org_id=order.org_id,
                contact_id=order.contact_id,
                action_type="gift",
                detail=f"{order.gift_name_snapshot} (one-off order, {order.fulfillment_method})",
                cost_cents=order.total_cents,
            ))

            db.session.add(ContactAuditLog(
                org_id=order.org_id,
                contact_id=order.contact_id,
                contact_name_snapshot=order.contact.household_name,
                actor_user_id=order.ordered_by_user_id,
                actor_name_snapshot=order.ordered_by.full_name if order.ordered_by else "Stripe checkout",
                action="gift_ordered",
                summary=(
                    f"{order.gift_name_snapshot} ordered and paid ({order.fulfillment_method}). "
                    f"Total ${order.total_cents / 100:.2f}."
                ),
            ))

            db.session.commit()
            send_order_confirmation(order)

    elif event["type"] == "customer.subscription.updated":
        # Fires for plan swaps and seat-quantity changes made through the
        # Stripe Billing Portal (or dashboard), and for status changes
        # like a failed renewal payment marking the subscription
        # past_due. Keep org.tier in sync with whatever's actually true
        # in Stripe rather than trusting anything the app itself thinks
        # it set earlier.
        sub = event["data"]["object"]
        org = Org.query.filter_by(stripe_subscription_id=sub["id"]).first()
        if org:
            if sub.get("status") == "canceled":
                org.tier = "free"
            else:
                price_id = sub["items"]["data"][0]["price"]["id"]
                tier = _tier_for_price_id(price_id)
                if tier:
                    org.tier = tier
                else:
                    current_app.logger.warning(
                        "customer.subscription.updated for org %s references unknown price %s",
                        org.id, price_id,
                    )
            db.session.commit()

    elif event["type"] == "customer.subscription.deleted":
        # Subscription fully ended (cancelled at period end, or
        # immediately) -- drop back to Free. Contacts/seats over the
        # Free limit aren't deleted; they just can't add more until
        # they're back under it (see Org.can_add_contact).
        sub = event["data"]["object"]
        org = Org.query.filter_by(stripe_subscription_id=sub["id"]).first()
        if org:
            org.tier = "free"
            org.stripe_subscription_id = None
            db.session.commit()

    return ("", 200)
