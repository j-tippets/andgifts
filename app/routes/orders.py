from datetime import datetime

from flask import Blueprint, render_template, request, current_app, abort, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Order, ActionLog, ContactAuditLog, Org, PaymentMethod
from app.services.stripe_client import get_stripe
from app.services.payments import charge_saved_card
from app.services.email import send_order_confirmation, send_wdf_fulfillment_notice
from app.services.org_events import record_org_event
from app.services.analytics import queue_event
from app.services import org_billing

orders_bp = Blueprint("orders", __name__)


def _own_pending_order(order_id):
    """Fetches an order this agent's org owns and that's still pending
    (i.e. mid-checkout) -- every step route in this flow needs the same
    guard, so it's centralized here rather than repeated per route."""
    return Order.query.filter_by(id=order_id, org_id=current_user.org_id).first_or_404()


@orders_bp.route("/orders/<order_id>/address", methods=["GET", "POST"])
@login_required
def collect_address(order_id):
    """Step 2 of the in-app checkout, only reached for a shipping order
    when the contact doesn't already have an address on file. Saves
    straight onto the Contact (not just this order) so it's there for
    next time -- see Contact.shipping_address_* and the "check the
    contact, ask once if blank" flow this was built around."""
    order = _own_pending_order(order_id)
    if order.status != "pending":
        return redirect(url_for("orders.order_success", order_id=order.id))
    contact = order.contact

    if request.method == "POST":
        contact.shipping_address_line1 = request.form.get("shipping_address_line1", "").strip() or None
        contact.shipping_address_line2 = request.form.get("shipping_address_line2", "").strip() or None
        contact.shipping_city = request.form.get("shipping_city", "").strip() or None
        contact.shipping_state = request.form.get("shipping_state", "").strip().upper() or None
        contact.shipping_zip = request.form.get("shipping_zip", "").strip() or None
        if not contact.has_shipping_address:
            flash("Fill in the full address to continue.", "error")
            return redirect(url_for("orders.collect_address", order_id=order.id))
        db.session.commit()
        queue_event("add_shipping_info", order_id=order.id)
        return redirect(url_for("orders.choose_payment", order_id=order.id))

    return render_template("orders/address.html", order=order, contact=contact)


@orders_bp.route("/orders/<order_id>/payment", methods=["GET", "POST"])
@login_required
def choose_payment(order_id):
    """Step 3: pick a saved card (or bounce to Settings to add one --
    see settings.add_payment_method's next_url support, which is what
    brings the agent back here afterward instead of stranding them on
    the Settings page mid-order)."""
    order = _own_pending_order(order_id)
    if order.status != "pending":
        return redirect(url_for("orders.order_success", order_id=order.id))
    if order.fulfillment_method == "shipping" and not order.contact.has_shipping_address:
        return redirect(url_for("orders.collect_address", order_id=order.id))

    if request.method == "POST":
        payment_method_id = request.form.get("payment_method_id", "").strip()
        card = PaymentMethod.query.filter_by(id=payment_method_id, user_id=current_user.id).first()
        if not card:
            flash("Choose a card to continue.", "error")
            return redirect(url_for("orders.choose_payment", order_id=order.id))
        order.payment_method_id = card.id
        db.session.commit()
        queue_event("add_payment_info", order_id=order.id, payment_type="saved_card")
        return redirect(url_for("orders.confirm_order", order_id=order.id))

    return render_template(
        "orders/payment.html", order=order, cards=current_user.payment_methods,
        stripe_configured=get_stripe() is not None,
    )


@orders_bp.route("/orders/<order_id>/confirm", methods=["GET", "POST"])
@login_required
def confirm_order(order_id):
    """Step 4: review contact/address/gift/payment, then charge on
    submit. A failed charge (see services.payments.charge_saved_card)
    leaves the order pending with the decline reason shown -- per
    Jeremiah's call, a failed charge blocks the approval/order rather
    than going through with an unpaid gift WDF would ship for free."""
    order = _own_pending_order(order_id)
    if order.status != "pending":
        return redirect(url_for("orders.order_success", order_id=order.id))
    if not order.payment_method_id:
        return redirect(url_for("orders.choose_payment", order_id=order.id))

    if request.method == "POST":
        queue_event(
            "checkout_submitted",
            items=[{"item_id": order.gift_catalog_item_id, "item_name": order.gift_name_snapshot,
                     "price": order.gift_price_cents / 100}],
            value=order.total_cents / 100,
        )
        success, intent_id, error = charge_saved_card(
            current_user, order.total_cents,
            description=f"{order.gift_name_snapshot} for {order.contact.household_name}",
            metadata={"order_id": order.id},
        )
        if not success:
            queue_event("gift_order_failed", failure_reason=error)
            flash(f"Payment failed: {error}", "error")
            return redirect(url_for("orders.confirm_order", order_id=order.id))

        order.status = "paid"
        order.paid_at = datetime.utcnow()
        order.stripe_payment_intent_id = intent_id
        if order.fulfillment_method == "shipping":
            order.shipping_address_snapshot = order.contact.formatted_shipping_address()

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
            actor_name_snapshot=order.ordered_by.full_name if order.ordered_by else "Unknown",
            action="gift_ordered",
            summary=(
                f"{order.gift_name_snapshot} ordered and paid ({order.fulfillment_method}). "
                f"Total ${order.total_cents / 100:.2f}."
            ),
        ))
        db.session.commit()
        queue_event(
            "purchase",
            transaction_id=order.id,
            value=order.total_cents / 100,
            currency="USD",
            items=[{"item_id": order.gift_catalog_item_id, "item_name": order.gift_name_snapshot,
                     "price": order.gift_price_cents / 100}],
        )
        send_order_confirmation(order)
        send_wdf_fulfillment_notice(order)
        return redirect(url_for("orders.order_success", order_id=order.id))

    return render_template("orders/confirm.html", order=order)


@orders_bp.route("/orders/<order_id>/success")
@login_required
def order_success(order_id):
    """Landing page after a successful in-app charge (see confirm_order,
    which sets status='paid' synchronously right before redirecting
    here -- unlike the old Stripe-Checkout-redirect flow, there's no
    webhook this needs to wait on, since the charge already happened
    server-side in the same request)."""
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
            elif tier == "team":
                # Team's onboarding-wizard checkout gets the fuller
                # validation (session status, subscription org_id,
                # price, customer match) and card-detail mirroring
                # that confirm_team_subscription_from_checkout_session
                # does -- see services/org_billing.py. This is the
                # SAME function routes/onboarding.billing_return calls
                # on the browser-return path, specifically so whichever
                # of the two runs first does the real work and the
                # other is a harmless re-save, never a double-apply.
                org_billing.confirm_team_subscription_from_checkout_session(org, session.get("id"))
            else:
                # Starter/Pro self-serve checkout (routes/billing.py) --
                # simpler by design: no onboarding wizard pre-selects a
                # tier ahead of payment for these, so there's no
                # "granted before confirmed" window to defend against
                # here the way there is for Team.
                old_tier = org.tier
                org.stripe_customer_id = session.get("customer")
                org.stripe_subscription_id = session.get("subscription")
                org.tier = tier
                if tier != old_tier:
                    record_org_event(org, "upgrade", old_tier, tier)
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
            old_tier = org.tier
            new_tier = None
            if sub.get("status") == "canceled":
                new_tier = "free"
            else:
                price_id = sub["items"]["data"][0]["price"]["id"]
                new_tier = _tier_for_price_id(price_id)
                if not new_tier:
                    current_app.logger.warning(
                        "customer.subscription.updated for org %s references unknown price %s",
                        org.id, price_id,
                    )

            # Only log/notify when the tier actually changed -- this
            # event also fires for things that don't affect tier at all
            # (e.g. a card update), and we don't want an email for those.
            if new_tier and new_tier != old_tier:
                org.tier = new_tier
                tier_order = current_app.config["TIER_ORDER"]
                event_type = "upgrade" if tier_order.index(new_tier) > tier_order.index(old_tier) else "downgrade"
                record_org_event(org, event_type, old_tier, new_tier)
            db.session.commit()

    elif event["type"] == "customer.subscription.deleted":
        # Subscription fully ended (cancelled at period end, or
        # immediately) -- drop back to Free. Contacts/seats over the
        # Free limit aren't deleted; they just can't add more until
        # they're back under it (see Org.can_add_contact).
        sub = event["data"]["object"]
        org = Org.query.filter_by(stripe_subscription_id=sub["id"]).first()
        if org:
            old_tier = org.tier
            org.tier = "free"
            org.stripe_subscription_id = None
            if old_tier != "free":
                record_org_event(org, "downgrade", old_tier, "free")
            db.session.commit()

    return ("", 200)
