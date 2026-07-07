import uuid
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


def gen_uuid():
    return str(uuid.uuid4())


class Org(db.Model):
    """
    Tenant boundary. Every contact, user, and gift action is scoped to an org_id.
    A single agent has one org with one user; a brokerage (team tier) has
    one org with many users.
    """
    __tablename__ = "orgs"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    name = db.Column(db.String(255), nullable=False)
    tier = db.Column(
        db.Enum("free", "starter", "pro", "team", name="org_tier"),
        nullable=False,
        default="free",
    )

    # Billing
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    billing_type = db.Column(
        db.Enum("card", "net30", "net60", name="billing_type"),
        nullable=False,
        default="card",
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = db.relationship("User", back_populates="org", cascade="all, delete-orphan")
    contacts = db.relationship("Contact", back_populates="org", cascade="all, delete-orphan")

    def contact_count(self):
        return len(self.contacts)

    def limit_for(self, key):
        from flask import current_app
        return current_app.config["TIER_LIMITS"][self.tier][key]

    def can_add_contact(self):
        limit = self.limit_for("contacts")
        return limit is None or self.contact_count() < limit

    def feature_enabled(self, key):
        return bool(self.limit_for(key))


class User(UserMixin, db.Model):
    """An agent (or admin) logging into the platform, scoped to one org."""
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False)

    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    role = db.Column(db.Enum("admin", "agent", name="user_role"), default="agent")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    org = db.relationship("Org", back_populates="users")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.email
