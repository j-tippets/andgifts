"""
Wrapper around SendGrid's Sender Verification API (Single Sender
Verification -- not domain authentication). Same "degrade gracefully if
the API key isn't configured" pattern as app/services/email.py and
app/services/stripe_client.py.

There is no webhook for "sender verified" -- the Event Webhook only
covers email activity (delivered/bounced/opened/etc), and the agent's
click on the confirmation link goes straight to SendGrid, not back to
us. So status is only ever refreshed by explicitly asking SendGrid via
get_sender_status(), called from the settings page on load and from a
manual "recheck" button -- see app/routes/profile.py.
"""
import requests
from flask import current_app

API_BASE = "https://api.sendgrid.com/v3/verified_senders"


def _headers():
    api_key = current_app.config.get("SENDGRID_API_KEY")
    if not api_key:
        return None
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def create_sender_identity(email, display_name):
    """Registers a new Sender Identity with SendGrid, which triggers
    SendGrid to email the agent a confirmation link. Returns the new
    sendgrid_sender_id (int) on success, or None on failure -- callers
    should flash a friendly error and leave the user's sender fields
    untouched when this returns None."""
    headers = _headers()
    if not headers:
        current_app.logger.warning("SendGrid not configured; skipping sender identity creation for %s", email)
        return None

    payload = {
        "nickname": display_name or email,
        "from_email": email,
        "from_name": display_name or email,
        "reply_to": email,
        "reply_to_name": display_name or email,
    }
    try:
        resp = requests.post(API_BASE, headers=headers, json=payload, timeout=10)
        if resp.status_code == 201:
            return resp.json().get("id")
        current_app.logger.error(
            "SendGrid create sender identity failed for %s: %s %s", email, resp.status_code, resp.text
        )
        return None
    except Exception as e:
        current_app.logger.error("SendGrid create sender identity error for %s: %s", email, e)
        return None


def get_sender_status(sendgrid_sender_id):
    """Returns True if verified, False if still pending, None if the
    lookup itself failed (SendGrid unreachable, id not found, etc) --
    callers should treat None as 'no change, try again later' rather
    than as 'not verified'."""
    headers = _headers()
    if not headers or not sendgrid_sender_id:
        return None

    try:
        resp = requests.get(API_BASE, headers=headers, params={"id": sendgrid_sender_id}, timeout=10)
        if resp.status_code != 200:
            current_app.logger.error(
                "SendGrid get sender status failed for id %s: %s %s",
                sendgrid_sender_id, resp.status_code, resp.text,
            )
            return None
        results = resp.json().get("results", [])
        match = next((r for r in results if r.get("id") == sendgrid_sender_id), None)
        if not match:
            return None
        return bool(match.get("verified"))
    except Exception as e:
        current_app.logger.error("SendGrid get sender status error for id %s: %s", sendgrid_sender_id, e)
        return None


def resend_verification(sendgrid_sender_id):
    """Re-triggers SendGrid's confirmation email for an existing, still-
    unverified identity. Returns True/False."""
    headers = _headers()
    if not headers or not sendgrid_sender_id:
        return False

    try:
        resp = requests.post(f"{API_BASE}/resend/{sendgrid_sender_id}", headers=headers, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        current_app.logger.error("SendGrid resend verification error for id %s: %s", sendgrid_sender_id, e)
        return False
