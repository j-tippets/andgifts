"""
Structured handoff to Wild Dog Fulfillment's own order-tracking tool --
a deliberately separate product/repo/database (see the WDF entity-
separation notes in business planning), not part of this app. This is
the one coupling point: a best-effort webhook POST, sent ALONGSIDE the
existing jtippets@outlook.com email (see services/email.py's
send_wdf_fulfillment_notice / send_wdf_handwritten_note_notice), not
instead of it -- belt-and-suspenders while the WDF tool is new and
unproven. Follows the same "degrade gracefully if unconfigured, never
raise" pattern as services/email.py and services/stripe_client.py: a
missing WDF_WEBHOOK_URL (e.g. before the WDF tool has been deployed
anywhere) just logs and returns False rather than breaking approval.
"""
from flask import current_app
import requests


def send_wdf_webhook(item_type, external_id, contact, agent, item_description,
                      price_cents, note_text=None, target_date=None, product_id=None):
    """POSTs one fulfillment item to the WDF tool's webhook endpoint.

    item_type: "gift" or "handwritten_note".
    external_id: this app's own id for the underlying record (Order.id
      for a gift, SuggestedAction.id for a note) -- not used by &Gifts
      itself, just carried along so WDF's tool can reference it if a
      question ever comes back the other way.
    contact/agent: Contact and User objects -- only their display
      fields are sent, never full records.
    target_date: the date this needs to ship by, if known (a gift's
      order_by from gift_timing, or a note's target_date), so the WDF
      tool can sort/flag by urgency the same way the Today dashboard
      already does. Optional since not every caller has one to hand.
    product_id: this app's GiftCatalogItem.id for a gift item, so WDF
      can match it against their own catalog instead of matching on
      item_description alone (which drifts if the item's name is
      later edited). None for a handwritten note, which isn't a
      catalog item.

    Returns True/False for whether the POST succeeded -- callers
    should treat this as informational only (log/flash at most) and
    never block approval on it, since the email notice is still the
    primary notification path.
    """
    url = current_app.config.get("WDF_WEBHOOK_URL")
    secret = current_app.config.get("WDF_WEBHOOK_SECRET")
    if not url:
        current_app.logger.info(
            "WDF_WEBHOOK_URL not configured; skipping structured handoff for %s %s",
            item_type, external_id,
        )
        return False

    payload = {
        "type": item_type,
        "external_id": external_id,
        "product_id": product_id,
        "agency_name": contact.org.name if contact.org else None,
        "agent_name": agent.full_name if agent else None,
        "recipient_name": contact.household_name,
        "recipient_address": contact.formatted_shipping_address(),
        "item_description": item_description,
        "price_cents": price_cents,
        "note_text": note_text,
        "target_date": target_date.isoformat() if target_date else None,
    }
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        if response.status_code >= 400:
            current_app.logger.warning(
                "WDF webhook returned %s for %s %s: %s",
                response.status_code, item_type, external_id, response.text[:500],
            )
            return False
        return True
    except requests.RequestException as exc:
        current_app.logger.warning(
            "WDF webhook failed for %s %s: %s", item_type, external_id, exc
        )
        return False
