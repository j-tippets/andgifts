from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid

# Built-in event types. Stored as a plain string column (not a hard DB enum)
# so agents can add custom milestones without a migration.
STANDARD_EVENT_TYPES = [
    "first_contact",
    "showing",
    "offer_made",
    "closing",
    "six_month_anniversary",
    "one_year_anniversary",
    "wedding_anniversary",
    "birthday",
    "custom",
]


class TimelineEvent(db.Model):
    """
    A single milestone on a contact's timeline. Some are one-time
    (showing, closing) and some recur annually (anniversaries, birthdays).
    Recurring events drive the daily suggestion engine.
    """
    __tablename__ = "timeline_events"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    contact_id = db.Column(db.String(36), db.ForeignKey("contacts.id"), nullable=False, index=True)

    event_type = db.Column(db.String(50), nullable=False)  # see STANDARD_EVENT_TYPES
    label = db.Column(db.String(150), nullable=True)  # custom display name, e.g. "Closed on Maple St house"
    event_date = db.Column(db.Date, nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)

    is_recurring = db.Column(db.Boolean, default=False)
    recurrence_rule = db.Column(
        db.Enum("annual", "none", name="recurrence_rule"), default="none"
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    contact = db.relationship("Contact", back_populates="timeline_events")

    def display_label(self):
        return self.label or self.event_type.replace("_", " ").title()
