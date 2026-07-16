"""
Transactional email via SendGrid. All sends are best-effort -- a failed
email should never break the request that triggered it (e.g. the Stripe
webhook confirming an order), so every public function catches and logs
rather than raises. Follows the same "degrade gracefully if the API key
isn't configured" pattern as app/services/llm.py.
"""
from flask import current_app


def _client():
    api_key = current_app.config.get("SENDGRID_API_KEY")
    if not api_key:
        return None
    try:
        from sendgrid import SendGridAPIClient
        return SendGridAPIClient(api_key)
    except Exception:
        return None


def send_email(to_email, subject, html_content):
    """Returns True if the email was handed off to SendGrid successfully,
    False otherwise. Never raises -- callers don't need to wrap this in
    try/except."""
    if not to_email:
        return False

    client = _client()
    if not client:
        current_app.logger.warning(
            "SendGrid not configured; skipping email to %s: %s", to_email, subject
        )
        return False

    try:
        from sendgrid.helpers.mail import Mail
        from_email = current_app.config.get("SENDGRID_FROM_EMAIL") or "orders@andgifts.app"
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            html_content=html_content,
        )
        client.send(message)
        return True
    except Exception as e:
        current_app.logger.error("SendGrid send failed to %s: %s", to_email, e)
        return False


def send_order_confirmation(order):
    """Order confirmation sent to the agent who placed it (not the
    contact) -- this is a receipt for what the agent bought on the
    client's behalf, not a marketing/client-facing email."""
    if not order.ordered_by or not order.ordered_by.email:
        return False

    if order.fulfillment_method == "pickup":
        fulfillment_line = f"Pickup at: {order.pickup_location or 'your shop'}"
    elif order.fulfillment_method == "dropoff":
        fulfillment_line = f"We'll drop this off at: {order.dropoff_location or 'your office'}"
    else:
        fulfillment_line = "Shipping to the address collected at checkout."

    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#2A1A45;">Order confirmed</h2>
      <p>Your gift order for <strong>{order.contact.household_name}</strong> is confirmed.</p>
      <table style="width:100%; border-collapse: collapse; margin: 16px 0;">
        <tr><td style="padding:6px 0; color:#6B6459;">Gift</td><td style="text-align:right;">{order.gift_name_snapshot}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459;">Gift price</td><td style="text-align:right;">${order.gift_price_cents / 100:.2f}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459;">Shipping</td><td style="text-align:right;">${(order.shipping_cost_cents or 0) / 100:.2f}</td></tr>
        <tr style="font-weight:bold; border-top:1px solid #eee;"><td style="padding:6px 0;">Total</td><td style="text-align:right;">${order.total_cents / 100:.2f}</td></tr>
      </table>
      <p>{fulfillment_line}</p>
      <p style="color:#6B6459; font-size:13px;">Order ID: {order.id}</p>
    </div>
    """

    return send_email(
        order.ordered_by.email,
        f"Order confirmed: {order.gift_name_snapshot} for {order.contact.household_name}",
        html,
    )
