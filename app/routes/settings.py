from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from urllib.parse import quote

from app.extensions import db
from app.decorators import admin_required
from app.models.org import Org, slugify_sender_local_part
from app.models import PaymentMethod
from app.services.stripe_client import get_stripe
from app.services import payments

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/")
@login_required
def index():
    """Settings hub. Deliberately thin -- it's a landing page that links
    out to the actual settings surfaces (custom fields today; team and
    profile are already reachable from here too, even though they also
    have their own direct links in the account menu). Add new settings
    sections here as they're built rather than growing this file itself."""
    return render_template("settings/index.html")


@settings_bp.route("/sender", methods=["GET", "POST"])
@admin_required
def sender_identity():
    """Org-admin-only: the shared From address flow-action emails send
    from for every agent in this org (see Org.sender_from). No
    per-agent verification -- the sending domain is authenticated once
    (outside the app, in SendGrid + DNS), so any local-part on it just
    works. Auto-generated at org creation; editable here afterward."""
    org = current_user.org

    if request.method == "POST":
        raw = request.form.get("sender_local_part", "").strip()
        candidate = slugify_sender_local_part(raw)
        if not candidate or (candidate == "agency" and raw):
            flash(
                "That name didn't leave anything usable after removing spaces/punctuation -- try letters and numbers.",
                "error",
            )
            return redirect(url_for("settings.sender_identity"))

        existing = Org.query.filter(Org.sender_local_part == candidate, Org.id != org.id).first()
        if existing:
            flash(f"\"{candidate}\" is already in use by another org on &Gifts -- try something more specific.", "error")
            return redirect(url_for("settings.sender_identity"))

        org.sender_local_part = candidate
        db.session.commit()
        flash("Sender address updated.", "success")
        return redirect(url_for("settings.sender_identity"))

    domain = current_app.config.get("SENDGRID_SENDING_DOMAIN")
    return render_template("settings/sender.html", org=org, domain=domain)


@settings_bp.route("/billing")
@admin_required
def billing():
    """Plan + usage overview, and the entry point into either starting a
    subscription (Free -> paid, via billing.checkout) or managing an
    existing one (via billing.portal, Stripe-hosted). See
    routes/billing.py for why upgrade and manage-existing are two
    different code paths."""
    org = current_user.org
    pricing_display = current_app.config["PRICING_DISPLAY"]
    tier_limits = current_app.config["TIER_LIMITS"]

    current_tier = {**pricing_display[org.tier], **tier_limits[org.tier]}
    other_self_serve_tiers = [
        {**pricing_display[t], **tier_limits[t], "tier_key": t}
        for t in ("starter", "pro")
        if t != org.tier
    ]

    return render_template(
        "settings/billing.html",
        org=org,
        current_tier=current_tier,
        other_self_serve_tiers=other_self_serve_tiers,
        contact_count=org.contact_count(),
        seat_count=org.seat_count(),
        email_sends_this_month=org.email_sends_this_month(),
    )


@settings_bp.route("/payment-methods")
@login_required
def payment_methods():
    """This agent's own saved cards for gift purchases -- per-agent, not
    per-org (see PaymentMethod's docstring). Used by both the manual
    order flow and automated flow approvals (services.payments.charge_saved_card)."""
    return render_template(
        "settings/payment_methods.html",
        cards=current_user.payment_methods,
        stripe_configured=get_stripe() is not None,
    )


@settings_bp.route("/payment-methods/add", methods=["POST"])
@login_required
def add_payment_method():
    """Starts a Stripe Setup Checkout session (mode='setup') to save a
    new card -- hosted on Stripe's own page, so there's no PCI burden
    and no custom card form to build. Returns to
    add_payment_method_return on completion, which is where the
    PaymentMethod actually gets saved (a session_id shows up in the
    query string there, same pattern as the existing gift-order and
    subscription checkouts).

    Accepts an optional next_url form field so the mid-order "add a
    new card" button (orders/payment.html) can send the agent back to
    finish the order they were placing instead of stranding them on
    the generic Settings page -- see add_payment_method_return, which
    carries it through Stripe's round trip and honors it on return."""
    next_url = request.form.get("next_url", "").strip() or url_for("settings.payment_methods")

    stripe, customer_id = payments.get_or_create_stripe_customer(current_user)
    if not stripe:
        flash("Payments aren't configured yet.", "error")
        return redirect(next_url)

    try:
        session = stripe.checkout.Session.create(
            mode="setup",
            customer=customer_id,
            payment_method_types=["card"],
            success_url=(
                url_for("settings.add_payment_method_return", _external=True)
                + "?session_id={CHECKOUT_SESSION_ID}&next="
                + quote(next_url, safe="")
            ),
            cancel_url=next_url,
        )
    except Exception as e:
        current_app.logger.error("Stripe setup session creation failed: %s", e)
        flash("Couldn't start card setup — please try again.", "error")
        return redirect(next_url)

    return redirect(session.url, code=303)


@settings_bp.route("/payment-methods/added")
@login_required
def add_payment_method_return():
    stripe = get_stripe()
    session_id = request.args.get("session_id")
    next_url = request.args.get("next") or url_for("settings.payment_methods")
    if not stripe or not session_id:
        flash("Couldn't confirm the card was saved — try adding it again.", "error")
        return redirect(next_url)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        current_app.logger.error("Stripe setup session retrieval failed: %s", e)
        flash("Couldn't confirm the card was saved — try adding it again.", "error")
        return redirect(next_url)

    saved = payments.save_payment_method_from_setup_intent(current_user, session.setup_intent)
    if saved:
        flash(f"{saved.display_label()} saved.", "success")
    else:
        flash("Couldn't confirm the card was saved — try adding it again.", "error")
    return redirect(next_url)


@settings_bp.route("/payment-methods/<payment_method_id>/default", methods=["POST"])
@login_required
def make_default_payment_method(payment_method_id):
    card = PaymentMethod.query.filter_by(id=payment_method_id, user_id=current_user.id).first_or_404()
    payments.set_default_payment_method(current_user, card.id)
    flash(f"{card.display_label()} is now your default card for automated flows.", "success")
    return redirect(url_for("settings.payment_methods"))


@settings_bp.route("/payment-methods/<payment_method_id>/remove", methods=["POST"])
@login_required
def remove_payment_method(payment_method_id):
    card = PaymentMethod.query.filter_by(id=payment_method_id, user_id=current_user.id).first_or_404()
    label = card.display_label()
    payments.remove_payment_method(current_user, card.id)
    flash(f"{label} removed.", "success")
    return redirect(url_for("settings.payment_methods"))
