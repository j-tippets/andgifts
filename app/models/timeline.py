from datetime import datetime
import re
from app.extensions import db
from app.models.org import gen_uuid

# The one milestone type that's still a code-level constant rather than
# data: the "type your own one-off label" escape hatch in every event-
# type dropdown (see routes/contacts._visible_event_types and the
# templates that check for this value directly, e.g.
# contacts/view.html's inline label field toggle). This applies to
# every org regardless of PracticeType, so it isn't a preset milestone
# -- there's nothing to seed or personalize about it.
#
# Everything that used to be a second, hardcoded tier of "built-in"
# milestones (closing, showing, birthday, ...) is gone -- those are now
# ordinary CustomEventType rows, seeded per org from whatever
# PracticeType preset the org started from (see
# services.practice_types and app/models/practice_types.py). An org can
# rename or remove them exactly like anything they added themselves.
CUSTOM_MILESTONE_KEY = "custom"


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

    event_type = db.Column(db.String(50), nullable=False)  # a CustomEventType.key, or CUSTOM_MILESTONE_KEY
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


class MilestonePriority(db.Model):
    """
    One agent's personal ranking of a milestone type, used to break ties
    when a contact qualifies for more than one milestone on the same day
    (see suggestion_engine._winning_event_for_contact). Deliberately
    tied to the AGENT, not the org or the milestone type itself -- two
    agents at the same agency can disagree about whether a closing
    outranks a birthday, and both are right for their own book of
    business.

    Only holds a row for a type the agent has actually placed in their
    drag-and-drop priority list (see routes/contacts.save_milestone_
    priority, which replaces an agent's full set on every save). A type
    with no row here -- because the agent never customized their order,
    or because it's a newer milestone added after they last saved --
    falls back to DEFAULT_MID_PRIORITY at lookup time. There's no
    separate per-type baked-in ranking anymore (the old global
    EVENT_TYPE_PRIORITY dict): everything starts equal until an agent
    says otherwise.

    event_type stores the same raw string as TimelineEvent.event_type
    (a CustomEventType.key, or the CUSTOM_MILESTONE_KEY sentinel) --
    not a foreign key, since a preset-seeded milestone is still just a
    regular CustomEventType row with no separate identity of its own.
    """
    __tablename__ = "milestone_priorities"
    __table_args__ = (
        db.UniqueConstraint("user_id", "event_type", name="uq_milestone_priority_user_event"),
    )

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False)

    # Higher number = more significant, same convention the old
    # EVENT_TYPE_PRIORITY dict used, so nothing about the comparison
    # logic in suggestion_engine.py had to change -- only where the
    # number comes from.
    priority = db.Column(db.Integer, nullable=False)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User")

    # Priority value assigned to any milestone type the agent hasn't
    # explicitly ranked -- everything starts on equal footing.
    DEFAULT_PRIORITY = 50

    @staticmethod
    def priority_for(user, event_type):
        if user is None:
            return MilestonePriority.DEFAULT_PRIORITY
        row = MilestonePriority.query.filter_by(user_id=user.id, event_type=event_type).first()
        return row.priority if row else MilestonePriority.DEFAULT_PRIORITY


class CustomEventType(db.Model):
    """
    A milestone type an org (admin-managed, shared) or an individual agent
    (personal, private to them) has -- either added themselves, or that
    started out as a PracticeType preset milestone (see
    app/models/practice_types.py) and got copied in as an ordinary row.
    Once copied, a preset milestone is indistinguishable from one the org
    invented themselves: fully renamable and removable, nothing about it
    is protected.

    `key` is what actually gets stored in TimelineEvent.event_type and
    Campaign/CampaignRecipe.event_type (all plain string columns), so it
    has to be unique within the org and can't collide with the
    CUSTOM_MILESTONE_KEY sentinel -- both enforced where these get
    created, not here.
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
