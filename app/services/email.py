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
        from_email = current_app.config.get("SENDGRID_FROM_EMAIL") or "notifications@andgifts.app"
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


def send_verification_email(user, verify_link):
    """Sent right after self-registration. The account can't log in
    (see User.is_active) until this link is clicked."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#2A1A45;">Confirm your email</h2>
      <p>Hi {user.first_name or 'there'} &mdash; one more step before you can sign in to &amp;Gifts.</p>
      <p><a href="{verify_link}" style="display:inline-block; background:#F77055; color:#fff; text-decoration:none; padding:10px 20px; border-radius:6px; font-weight:bold;">Verify my email</a></p>
      <p style="color:#6B6459; font-size:13px;">This link expires in 48 hours. If you didn't create an &amp;Gifts account, you can ignore this email.</p>
    </div>
    """
    return send_email(user.email, "Confirm your &Gifts account", html)


def send_password_reset_email(user, reset_link):
    """Sent from the 'forgot password' flow. Safe to call for any user --
    the calling route is responsible for not leaking whether an account
    exists (see auth.forgot_password)."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#2A1A45;">Reset your password</h2>
      <p>We got a request to reset the password on your &amp;Gifts account.</p>
      <p><a href="{reset_link}" style="display:inline-block; background:#F77055; color:#fff; text-decoration:none; padding:10px 20px; border-radius:6px; font-weight:bold;">Choose a new password</a></p>
      <p style="color:#6B6459; font-size:13px;">This link expires in 1 hour. If you didn't request this, you can safely ignore this email -- your password won't change.</p>
    </div>
    """
    return send_email(user.email, "Reset your &Gifts password", html)


def send_team_invite_email(user, invite_link, inviter_name):
    """Sent when an admin invites a new agent by email (as opposed to
    setting a temp password directly). The account stays in 'pending'
    status until this link is clicked and a password is set."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#2A1A45;">You're invited to &amp;Gifts</h2>
      <p>{inviter_name} invited you to join {user.org.name} on &amp;Gifts.</p>
      <p><a href="{invite_link}" style="display:inline-block; background:#F77055; color:#fff; text-decoration:none; padding:10px 20px; border-radius:6px; font-weight:bold;">Accept invite &amp; set your password</a></p>
      <p style="color:#6B6459; font-size:13px;">This link expires in 7 days.</p>
    </div>
    """
    return send_email(user.email, f"You're invited to join {user.org.name} on &Gifts", html)


def send_flow_action_email(action, sender_name):
    """Sends an approved flow 'email' action's message to the contact.
    Returns (delivered, error_message) -- error_message is None on
    success, and set to a short human-readable reason on failure (no
    email on file, or the SendGrid send itself failing) so it can be
    stored on the ActionLog and shown in the reports."""
    to_email = action.contact.primary_email()
    if not to_email:
        return False, "No email address on file for this contact."

    body_text = action.generated_message or action.reason_text
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <p>{body_text}</p>
      <p style="color:#6B6459; font-size:13px;">&mdash; {sender_name}</p>
    </div>
    """
    subject = f"A note from {sender_name}"

    delivered = send_email(to_email, subject, html)
    if not delivered:
        if not current_app.config.get("SENDGRID_API_KEY"):
            return False, "SendGrid isn't configured for this environment."
        return False, "SendGrid send failed. Check the app logs, or try sending manually."
    return True, None

def send_support_request(user, topic, message):
    """Sent when a user submits the Support form (see routes/support.py).
    Goes to the internal support inbox, not the user -- this is a report
    of an issue, not a user-facing notification."""
    to_email = current_app.config.get("SUPPORT_INBOX_EMAIL")
    if not to_email:
        return False

    org_name = user.org.name if user.org else "(no org)"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#2A1A45;">New support request</h2>
      <table style="width:100%; border-collapse: collapse; margin: 16px 0;">
        <tr><td style="padding:6px 0; color:#6B6459; width:120px;">Company</td><td>{org_name}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459;">User</td><td>{user.full_name}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459;">Email</td><td>{user.email}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459;">Topic</td><td>{topic}</td></tr>
      </table>
      <p style="color:#6B6459; font-size:13px; margin-bottom:4px;">Message</p>
      <p style="white-space: pre-wrap;">{message}</p>
    </div>
    """
    return send_email(to_email, f"&Gifts support: {topic} ({org_name})", html)


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
