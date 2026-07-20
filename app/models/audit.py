from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class ContactAuditLog(db.Model):
    """
    Append-only activity trail for a contact: who did what, when, in plain
    English. contact_id and actor_user_id are both nullable and paired with
    denormalized name snapshots, so a log entry stays legible even after the
    contact is deleted or the acting user's account is removed later --
    neither of those cascades into deleting history.
    """
    __tablename__ = "contact_audit_log"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)
    contact_id = db.Column(db.String(36), db.ForeignKey("contacts.id"), nullable=True, index=True)
    contact_name_snapshot = db.Column(db.String(255), nullable=False)

    actor_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    actor_name_snapshot = db.Column(db.String(150), nullable=False)

    action = db.Column(
        db.Enum(
            "created", "updated", "status_changed", "reassigned",
            "timeline_added", "timeline_updated", "deleted", "gift_ordered",
            "action_deleted", "action_undeleted",
            "action_suggested", "action_approved", "action_unapproved",
            name="contact_audit_action",
        ),
        nullable=False,
    )
    summary = db.Column(db.Text, nullable=False)

    # Only set for action_deleted/action_undeleted entries -- lets the
    # recent-activity list render an "Undelete" button that goes straight
    # back to the SuggestedAction row. Nullable and not cascaded so the log
    # entry stays legible even if the suggestion itself is later hard-deleted.
    suggested_action_id = db.Column(db.String(36), db.ForeignKey("suggested_actions.id"), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    contact = db.relationship("Contact")
    actor = db.relationship("User")
    suggested_action = db.relationship("SuggestedAction")
