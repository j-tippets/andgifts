from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid

BADGE_SCOPES = ("global", "personal")

# Many-to-many: contacts <-> badges
contact_badges = db.Table(
    "contact_badges",
    db.Column("contact_id", db.String(36), db.ForeignKey("contacts.id"), primary_key=True),
    db.Column("badge_id", db.String(36), db.ForeignKey("badges.id"), primary_key=True),
)


class Badge(db.Model):
    """
    A short label an agent (or the platform) can attach to a Contact --
    e.g. "VIP", "Past Client Referral Source". Distinct from Interest
    (drives gift-catalog matching) and CustomFieldDefinition (arbitrary
    structured data): a badge is a lightweight on/off tag meant to show
    up as a pill on the contact and to be checked by a flow condition
    ("has badge X"), same treatment as interest_tag in campaign_rules.py.

    scope="global": created by a platform_admin, org_id is NULL --
    visible to and usable by every org on &Gifts. This is where
    something like "VIP" lives if every agency should have it out of
    the box.
    scope="personal": created by one agent, owner_user_id set, org_id
    is that agent's org -- visible/usable only by that agent, same
    convention as CustomFieldDefinition/CustomEventType personal scope.
    There's deliberately no org-wide (admin-managed-per-agency) tier --
    just platform-global and personal, matching what was asked for.
    """
    __tablename__ = "badges"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=True, index=True)

    scope = db.Column(db.Enum(*BADGE_SCOPES, name="badge_scope"), nullable=False)
    owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)

    label = db.Column(db.String(50), nullable=False)
    # Optional accent color for the pill, e.g. "#c9a86a". Falls back to
    # a default style in the UI when unset.
    color = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[owner_user_id])
    contacts = db.relationship("Contact", secondary=contact_badges, back_populates="badges")

    @staticmethod
    def visible_to(query, user):
        """Every global badge, plus this user's own personal ones."""
        return query.filter(
            (Badge.scope == "global") | (Badge.owner_user_id == user.id)
        )
