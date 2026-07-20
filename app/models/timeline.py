from datetime import datetime
import re
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


def slugify_event_key(label):
    """Turn a milestone name into the string that actually gets stored
    in event_type columns -- lowercase, underscored, alnum only."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "milestone"


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


class CustomEventType(db.Model):
    """
    A milestone type an org (admin-managed, shared) or an individual agent
    (personal, private to them) has added on top of STANDARD_EVENT_TYPES --
    so an agency can track whatever "key milestones" matter to them (and
    build flows off of), not just the handful every agency starts with.

    `key` is what actually gets stored in TimelineEvent.event_type and
    Campaign/CampaignRecipe.event_type (all plain string columns, see the
    note above STANDARD_EVENT_TYPES), so it has to be unique within the
    org and can't collide with a built-in type -- both enforced where
    these get created, not here.
    """
    __tablename__ = "custom_event_types"
    __table_args__ = (
        db.UniqueConstraint("org_id", "key", name="uq_custom_event_type_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)

    # "org"      = defined by an admin, usable by every agent in the org.
    # "personal" = defined by one agent, usable only by that agent.
    scope = db.Column(db.Enum("org", "personal", name="custom_event_type_scope"), nullable=False)
    owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)

    key = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(100), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[owner_user_id])

    @staticmethod
    def visible_to(query, user):
        """Org-wide milestones, plus this user's own personal ones --
        same visibility rule as CustomFieldDefinition."""
        return query.filter(
            (CustomEventType.scope == "org") | (CustomEventType.owner_user_id == user.id)
        )
