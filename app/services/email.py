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


def send_email(to_email, subject, html_content, from_email=None, from_name=None, reply_to=None, reply_to_name=None):
    """Returns True if the email was handed off to SendGrid successfully,
    False otherwise. Never raises -- callers don't need to wrap this in
    try/except.

    from_email/from_name let a caller send as a specific org sender
    (see send_flow_action_email); when omitted, falls back to the
    generic notifications@andgifts.app address as before.
    reply_to/reply_to_name are independent of from_email -- they control
    where a client's "Reply" actually lands, not what shows as sender."""
    if not to_email:
        return False

    client = _client()
    if not client:
        current_app.logger.warning(
            "SendGrid not configured; skipping email to %s: %s", to_email, subject
        )
        return False

    try:
        from sendgrid.helpers.mail import Mail, From, ReplyTo
        default_from = current_app.config.get("SENDGRID_FROM_EMAIL") or "notifications@andgifts.app"
        sender = From(from_email, from_name) if from_email else default_from
        message = Mail(
            from_email=sender,
            to_emails=to_email,
            subject=subject,
            html_content=html_content,
        )
        if reply_to:
            message.reply_to = ReplyTo(reply_to, reply_to_name)
        client.send(message)
        return True
    except Exception as e:
        # sendgrid-python raises python_http_client's HTTPError for any
        # non-2xx response. Its str() is just the status line -- the
        # actually useful part (which sender/domain/reason SendGrid
        # rejected) is in .body, so surface that explicitly rather than
        # leaving it to a bare "%s" of the exception.
        body = getattr(e, "body", None)
        if body:
            try:
                body = body.decode("utf-8")
            except AttributeError:
                pass
            current_app.logger.error(
                "SendGrid send failed to %s (status %s): %s",
                to_email, getattr(e, "status_code", "?"), body,
            )
        else:
            current_app.logger.error("SendGrid send failed to %s: %s", to_email, e)
        return False


def _button(url, label):
    """Shared CTA button styling -- coral fill, rounded, used across every
    templated email so buttons look identical no matter which one fires."""
    return (
        f'<a href="{url}" style="display:inline-block; background:#F77055; '
        f'color:#ffffff; text-decoration:none; padding:12px 28px; '
        f'border-radius:8px; font-family:\'Baloo 2\', Arial, sans-serif; '
        f'font-weight:700; font-size:15px;">{label}</a>'
    )


def _wrap_email(body_html, preheader=""):
    """Wraps templated inner content in the shared &Gifts branded shell:
    header wordmark, cream card on a soft page background, standard
    footer. Every send_*_email function below builds its content and
    passes it through here, so brand styling lives in exactly one place.

    Uses a table-based layout (not flex/grid) because that's what
    actually renders consistently across Outlook desktop, Gmail, and
    Apple Mail -- modern CSS layout support is inconsistent across email
    clients in a way it isn't for browsers.

    preheader is the short snippet inbox previews show next to the
    subject line (Gmail/Outlook list view) -- without one, clients fall
    back to grabbing the first visible text, which is usually an
    unhelpful "Hi ," or a stray heading. Hidden from the rendered body,
    visible only in the inbox list.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>&amp;Gifts</title>
</head>
<body style="margin:0; padding:0; background:#f8f6f6; font-family:'Inter', Arial, sans-serif;">
  <div style="display:none; max-height:0; overflow:hidden; opacity:0;">{preheader}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8f6f6;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" style="max-width:480px;" cellpadding="0" cellspacing="0">
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <span style="font-family:'Baloo 2', Arial, sans-serif; font-size:28px; font-weight:700;">
                <span style="color:#F77055;">&amp;</span><span style="color:#2A1A45;">Gifts</span>
              </span>
            </td>
          </tr>
          <tr>
            <td style="background:#fbf5f1; border-radius:16px; padding:32px 28px;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:24px 16px 0; font-family:'Inter', Arial, sans-serif; font-size:12px; color:#6B6459; line-height:1.6;">
              &amp;Gifts &middot; a Wyld Totems LLC product<br>
              <!-- TODO(jeremiah): add a mailing address here -- most spam filters and CAN-SPAM
                   both expect a physical postal address in the footer of commercial email. -->
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_verification_email(user, verify_link):
    """Sent right after self-registration. The account can't log in
    (see User.is_active) until this link is clicked."""
    body = f"""
      <h2 style="margin:0 0 12px; color:#2A1A45; font-family:'Besley', Georgia, serif; font-size:22px;">Confirm your email</h2>
      <p style="margin:0 0 20px; color:#2A1A45; font-size:15px; line-height:1.6;">Hi {user.first_name or 'there'} &mdash; one more step before you can sign in to &amp;Gifts.</p>
      <p style="margin:0 0 20px;">{_button(verify_link, 'Verify my email')}</p>
      <p style="margin:0; color:#6B6459; font-size:13px;">This link expires in 48 hours. If you didn't create an &amp;Gifts account, you can ignore this email.</p>
    """
    html = _wrap_email(body, preheader="One more step before you can sign in to &Gifts.")
    return send_email(user.email, "Confirm your &Gifts account", html)


def send_password_reset_email(user, reset_link):
    """Sent from the 'forgot password' flow. Safe to call for any user --
    the calling route is responsible for not leaking whether an account
    exists (see auth.forgot_password)."""
    body = f"""
      <h2 style="margin:0 0 12px; color:#2A1A45; font-family:'Besley', Georgia, serif; font-size:22px;">Reset your password</h2>
      <p style="margin:0 0 20px; color:#2A1A45; font-size:15px; line-height:1.6;">We got a request to reset the password on your &amp;Gifts account.</p>
      <p style="margin:0 0 20px;">{_button(reset_link, 'Choose a new password')}</p>
      <p style="margin:0; color:#6B6459; font-size:13px;">This link expires in 1 hour. If you didn't request this, you can safely ignore this email -- your password won't change.</p>
    """
    html = _wrap_email(body, preheader="Reset the password on your &Gifts account.")
    return send_email(user.email, "Reset your &Gifts password", html)


def send_team_invite_email(user, invite_link, inviter_name):
    """Sent when an admin invites a new agent by email (as opposed to
    setting a temp password directly). The account stays in 'pending'
    status until this link is clicked and a password is set."""
    body = f"""
      <h2 style="margin:0 0 12px; color:#2A1A45; font-family:'Besley', Georgia, serif; font-size:22px;">You're invited to &amp;Gifts</h2>
      <p style="margin:0 0 20px; color:#2A1A45; font-size:15px; line-height:1.6;">{inviter_name} invited you to join {user.org.name} on &amp;Gifts.</p>
      <p style="margin:0 0 20px;">{_button(invite_link, 'Accept invite &amp; set your password')}</p>
      <p style="margin:0; color:#6B6459; font-size:13px;">This link expires in 7 days.</p>
    """
    html = _wrap_email(body, preheader=f"{inviter_name} invited you to join {user.org.name} on &Gifts.")
    return send_email(user.email, f"You're invited to join {user.org.name} on &Gifts", html)


def send_account_created_email(user, inviter_name, login_link):
    """Sent when an admin creates an agent's account directly with a
    temp password (as opposed to the email-invite flow above). The
    account is already active by the time this sends -- this is just a
    heads-up, not a required step. Deliberately does NOT include the
    temp password itself; that's shown to the admin once (see
    team.new_member) and expected to be shared out-of-band, so this
    email doesn't become a second, harder-to-revoke copy of it sitting
    in an inbox."""
    body = f"""
      <h2 style="margin:0 0 12px; color:#2A1A45; font-family:'Besley', Georgia, serif; font-size:22px;">Your &amp;Gifts account is ready</h2>
      <p style="margin:0 0 20px; color:#2A1A45; font-size:15px; line-height:1.6;">{inviter_name} set you up with an account for {user.org.name} on &amp;Gifts. Ask {inviter_name} for your temporary password to sign in.</p>
      <p style="margin:0 0 20px;">{_button(login_link, 'Sign in')}</p>
      <p style="margin:0; color:#6B6459; font-size:13px;">You can change your password once you're signed in.</p>
    """
    html = _wrap_email(body, preheader=f"{inviter_name} set you up with an &Gifts account for {user.org.name}.")
    return send_email(user.email, f"Your &Gifts account for {user.org.name} is ready", html)


def send_flow_action_email(action, sender_name, sender_user=None):
    """Sends an approved flow 'email' action's message to the contact.
    Returns (delivered, error_message) -- error_message is None on
    success, and set to a short human-readable reason on failure (no
    email on file, or the SendGrid send itself failing) so it can be
    stored on the ActionLog and shown in the reports.

    From is the sending org's shared address on our domain-authenticated
    sending domain (see Org.sender_from) -- no per-agent verification
    needed. Reply-To is sender_user's own account email, so a client
    hitting "Reply" still reaches the actual agent even though the
    visible From address is shared org-wide."""
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

    from_email, from_name = sender_user.org.sender_from() if sender_user and sender_user.org else (None, None)
    reply_to, reply_to_name = (sender_user.email, sender_name) if sender_user else (None, None)
    delivered = send_email(
        to_email, subject, html,
        from_email=from_email, from_name=from_name,
        reply_to=reply_to, reply_to_name=reply_to_name,
    )
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

    body = f"""
      <h2 style="margin:0 0 12px; color:#2A1A45; font-family:'Besley', Georgia, serif; font-size:22px;">Order confirmed</h2>
      <p style="margin:0 0 16px; color:#2A1A45; font-size:15px; line-height:1.6;">Your gift order for <strong>{order.contact.household_name}</strong> is confirmed.</p>
      <table role="presentation" style="width:100%; border-collapse: collapse; margin: 0 0 16px;">
        <tr><td style="padding:6px 0; color:#6B6459; font-size:14px;">Gift</td><td style="text-align:right; font-size:14px; color:#2A1A45;">{order.gift_name_snapshot}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459; font-size:14px;">Gift price</td><td style="text-align:right; font-size:14px; color:#2A1A45;">${order.gift_price_cents / 100:.2f}</td></tr>
        <tr><td style="padding:6px 0; color:#6B6459; font-size:14px;">Shipping</td><td style="text-align:right; font-size:14px; color:#2A1A45;">${(order.shipping_cost_cents or 0) / 100:.2f}</td></tr>
        <tr style="font-weight:bold; border-top:1px solid rgba(42,26,69,0.12);"><td style="padding:6px 0; font-size:14px; color:#2A1A45;">Total</td><td style="text-align:right; font-size:14px; color:#2A1A45;">${order.total_cents / 100:.2f}</td></tr>
      </table>
      <p style="margin:0 0 16px; color:#2A1A45; font-size:15px;">{fulfillment_line}</p>
      <p style="margin:0; color:#6B6459; font-size:13px;">Order ID: {order.id}</p>
    """
    html = _wrap_email(body, preheader=f"Your gift order for {order.contact.household_name} is confirmed.")

    return send_email(
        order.ordered_by.email,
        f"Order confirmed: {order.gift_name_snapshot} for {order.contact.household_name}",
        html,
    )
