from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class SupportRequest(db.Model):
    """
    A submission of the Support form (see routes/support.py). Logged
    purely as a record/inbox view inside the app -- the actual
    notification is the email sent to SUPPORT_INBOX_EMAIL at submit time.
    org_id/user_id are nullable and paired with denormalized snapshots so
    a request stays legible even if the org or user is later removed.
    """
    __tablename__ = "support_requests"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)

    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=True, index=True)
    org_name_snapshot = db.Column(db.String(255), nullable=False)

    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    user_name_snapshot = db.Column(db.String(150), nullable=False)
    user_email_snapshot = db.Column(db.String(255), nullable=False)

    topic = db.Column(db.String(64), nullable=False)
    message = db.Column(db.Text, nullable=False)

    # Whether the notification email to SUPPORT_INBOX_EMAIL actually went
    # out -- lets an admin view distinguish "sent" requests from ones that
    # only landed here because SendGrid/email delivery failed.
    email_delivered = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    org = db.relationship("Org")
    user = db.relationship("User")
