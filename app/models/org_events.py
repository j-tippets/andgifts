from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid

EVENT_TYPES = ("signup", "upgrade", "downgrade")


class OrgEventLog(db.Model):
    """
    A record of a business-significant lifecycle event for an org: a new
    signup, or a plan change in either direction. Two purposes: this is
    the source of truth the App Admin activity page reads from, AND the
    trigger for the admin notification email (see
    services/org_events.record_org_event) -- so the two can never drift
    out of sync the way an email-only or log-only implementation would.

    org_name_snapshot follows the same pattern as SupportRequest: kept
    even if the org is later deleted, so history stays legible.
    """
    __tablename__ = "org_event_log"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)

    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=True, index=True)
    org_name_snapshot = db.Column(db.String(255), nullable=False)

    event_type = db.Column(db.Enum(*EVENT_TYPES, name="org_event_type"), nullable=False)
    # from_tier is null for a signup (there's no prior tier); always set
    # for upgrade/downgrade.
    from_tier = db.Column(db.String(20), nullable=True)
    to_tier = db.Column(db.String(20), nullable=False)

    # Whether the notification email to PLATFORM_ADMIN_EMAIL actually
    # went out -- same reasoning as SupportRequest.email_delivered, so
    # the activity page can flag events Jeremiah might not have heard
    # about any other way.
    email_delivered = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    org = db.relationship("Org")
