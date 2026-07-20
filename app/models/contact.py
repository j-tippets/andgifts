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
    photo_url = db.Column(db.String(500), nullable=True)  # helps an agent recognize the household at a glance
    status = db.Column(
        db.Enum("new", "active", "past", name="contact_status"),
        default="new",
        index=True,
    )
    notes = db.Column(db.Text, nullable=True)

    # Marketing opt-out: stop including this household in bulk/marketing
    # sends. Relationship-driven milestone gifts (birthdays, closing
    # anniversaries, etc.) are NOT marketing and are unaffected by this flag.
    marketing_opt_out = db.Column(db.Boolean, default=False, nullable=False)

    # Do Not Contact: a hard stop. No gifts, emails, texts, or suggestions
    # of any kind should be generated for this household once this is set.
    do_not_contact = db.Column(db.Boolean, default=False, nullable=False)

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
    custom_values = db.relationship(
        "CustomFieldValue", back_populates="contact", cascade="all, delete-orphan"
    )

    def primary_person(self):
        return next((p for p in self.people if p.household_role == "head"), self.people[0] if self.people else None)

    def primary_email(self):
        """Best email address to reach this household at: the head of
        household's primary email if set, else the first email found on
        the head, else the first email found on anyone in the household.
        Returns None if nobody has one on file."""
        head = self.primary_person()
        if head:
            primary = next(
                (m.value for m in head.contact_methods if m.method_type == "email" and m.is_primary), None
            )
            if primary:
                return primary
            any_head_email = next((m.value for m in head.contact_methods if m.method_type == "email"), None)
            if any_head_email:
                return any_head_email
        for person in self.people:
            for method in person.contact_methods:
                if method.method_type == "email":
                    return method.value
        return None

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


CUSTOM_FIELD_TYPES = ["text", "textarea", "number", "date", "checkbox", "select"]


class CustomFieldDefinition(db.Model):
    """
    A custom field an org (admin-managed, shared) or an individual agent
    (personal, private to them) has added to the Contact record. The actual
    per-contact data lives in CustomFieldValue.
    """
    __tablename__ = "custom_field_definitions"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)

    # "org"      = defined by an admin, visible/usable by every agent in the org.
    # "personal" = defined by one agent, visible/usable only by that agent.
    scope = db.Column(db.Enum("org", "personal", name="custom_field_scope"), nullable=False)
    owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)

    label = db.Column(db.String(100), nullable=False)
    field_type = db.Column(db.Enum(*CUSTOM_FIELD_TYPES, name="custom_field_type"), nullable=False, default="text")
    # For "select" fields: comma-separated list of options, e.g. "Gold,Silver,Bronze".
    options = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[owner_user_id])
    values = db.relationship(
        "CustomFieldValue", back_populates="field_definition", cascade="all, delete-orphan"
    )

    def option_list(self):
        return [o.strip() for o in (self.options or "").split(",") if o.strip()]

    @staticmethod
    def visible_to(query, user):
        """Org-wide fields, plus this user's own personal fields."""
        return query.filter(
            (CustomFieldDefinition.scope == "org")
            | (CustomFieldDefinition.owner_user_id == user.id)
        )


class CustomFieldValue(db.Model):
    """One field's value for one contact."""
    __tablename__ = "custom_field_values"
    __table_args__ = (
        db.UniqueConstraint("contact_id", "field_definition_id", name="uq_custom_field_value"),
    )

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    contact_id = db.Column(db.String(36), db.ForeignKey("contacts.id"), nullable=False, index=True)
    field_definition_id = db.Column(
        db.String(36), db.ForeignKey("custom_field_definitions.id"), nullable=False, index=True
    )
    value = db.Column(db.Text, nullable=True)

    contact = db.relationship("Contact", back_populates="custom_values")
    field_definition = db.relationship("CustomFieldDefinition", back_populates="values")
