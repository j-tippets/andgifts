"""
Single call site for logging + notifying on org lifecycle events
(signup, upgrade, downgrade). Kept as one function specifically so the
OrgEventLog row and the admin notification email can never drift out
of sync -- every caller (registration, and each relevant Stripe
webhook branch) goes through here rather than duplicating the
"log this AND email Jeremiah" pairing at each call site.

Does NOT commit -- adds the OrgEventLog row to the session and lets
the caller's existing db.session.commit() (which is already
committing the org/tier change itself) cover it in the same
transaction.
"""
from flask import url_for

from app.extensions import db
from app.models.org_events import OrgEventLog
from app.services.email import send_org_event_notification


def record_org_event(org, event_type, from_tier, to_tier):
    org_admin_url = None
    try:
        org_admin_url = url_for("app_admin.org_edit", org_id=org.id, _external=True)
    except Exception:
        # url_for can fail outside a request/app context in edge cases
        # (e.g. a management shell) -- the notification is still worth
        # sending without the link rather than not sending at all.
        pass

    delivered = send_org_event_notification(org, event_type, from_tier, to_tier, org_admin_url)

    db.session.add(OrgEventLog(
        org_id=org.id,
        org_name_snapshot=org.name,
        event_type=event_type,
        from_tier=from_tier,
        to_tier=to_tier,
        email_delivered=delivered,
    ))
