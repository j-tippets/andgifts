from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid

# Many-to-many: contacts <-> interests
contact_interests = db.Table(
    "contact_interests",
    db.Column("contact_id", db.String(36), db.ForeignKey("contacts.id"), primary_key=True),
    db.Column("interest_id", db.String(36), db.ForeignKey("interests.id"), primary_key=True),
)


class Contact(db.Model):
    """
    A household / client record. This is the top-level thing an agent
    thinks of as "a client" -- e.g. 'The Smiths'. Individual people
    (head of household, spouse, etc.) live in ContactPerson below.
    """
    __tablename__ = "contacts"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)

    # NULL = shared org-wide contact, visible to every agent in the agency.
    # Set = private to that one agent (admins can still see everything).
    owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)

    household_name = db.Column(db.String(255), nullable=False)  # "The Smiths"
    status = db.Column(
        db.Enum("new", "active", "past", name="contact_status"),
        default="new",
        index=True,
    )
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    org = db.relationship("Org", back_populates="contacts")
    owner = db.relationship("User", foreign_keys=[owner_user_id])
    people = db.relationship("ContactPerson", back_populates="contact", cascade="all, delete-orphan")
    timeline_events = db.relationship(
        "TimelineEvent", back_populates="contact", cascade="all, delete-orphan",
        order_by="TimelineEvent.event_date",
    )
    interests = db.relationship("Interest", secondary=contact_interests, back_populates="contacts")

    def primary_person(self):
        return next((p for p in self.people if p.household_role == "head"), self.people[0] if self.people else None)

    @staticmethod
    def visible_to(query, user):
        """
        Scope a Contact query to what `user` is allowed to see:
        admins see every contact in the org; agents see shared (org-wide)
        contacts plus their own private ones.
        """
        if user.is_admin:
            return query
        return query.filter(
            (Contact.owner_user_id.is_(None)) | (Contact.owner_user_id == user.id)
        )


class ContactPerson(db.Model):
    """An individual within a household (head, spouse, other)."""
    __tablename__ = "contact_people"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    contact_id = db.Column(db.String(36), db.ForeignKey("contacts.id"), nullable=False, index=True)

    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    household_role = db.Column(
        db.Enum("head", "spouse", "other", name="household_role"), default="head"
    )
    birthday = db.Column(db.Date, nullable=True)

    contact = db.relationship("Contact", back_populates="people")
    contact_methods = db.relationship(
        "ContactMethod", back_populates="person", cascade="all, delete-orphan"
    )

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class ContactMethod(db.Model):
    """Email or phone belonging to a specific ContactPerson."""
    __tablename__ = "contact_methods"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    person_id = db.Column(db.String(36), db.ForeignKey("contact_people.id"), nullable=False, index=True)

    method_type = db.Column(db.Enum("email", "phone", name="method_type"), nullable=False)
    subtype = db.Column(
        db.Enum("personal", "work", "mobile", "home", name="method_subtype"),
        default="personal",
    )
    value = db.Column(db.String(255), nullable=False)
    is_primary = db.Column(db.Boolean, default=False)

    person = db.relationship("ContactPerson", back_populates="contact_methods")


class Interest(db.Model):
    """Global tag list (golf, football, wine, kids sports, etc.)."""
    __tablename__ = "interests"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    name = db.Column(db.String(100), unique=True, nullable=False)

    contacts = db.relationship("Contact", secondary=contact_interests, back_populates="interests")
