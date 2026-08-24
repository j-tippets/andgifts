import re
import uuid
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


def gen_uuid():
    return str(uuid.uuid4())


def slugify_sender_local_part(name):
    """Turn an org name into an email local-part: lowercase, alnum only,
    no separator (matches the 'NorthStarRealty' style, not
    'north_star_realty'). Falls back to 'agency' for names that are
    entirely punctuation/emoji."""
    slug = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return slug or "agency"


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

    # Which preset milestone set this org started from (Real Estate, Law
    # Firm, ...) -- see PracticeType and services.practice_types. Kept
    # after the org's milestones are seeded/personalized purely so App
    # Admin can show/change it later; nothing at request time re-derives
    # milestone behavior from this, since seeded CustomEventType rows
    # are the org's own from that point on.
    practice_type_id = db.Column(db.String(36), db.ForeignKey("practice_types.id"), nullable=True)

    # Billing
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    billing_type = db.Column(
        db.Enum("card", "net30", "net60", name="billing_type"),
        nullable=False,
        default="card",
    )

    # --- Org-level subscription card (Team signup wizard) ---
    # Captured via a Stripe SetupIntent during onboarding (see
    # services/org_billing.py) and saved WITHOUT charging it --
    # Team is custom-priced (no STRIPE_PRICE_IDS entry), so there's no
    # subscription to actually start yet. This just puts a card on
    # file so Jeremiah can complete billing setup (Stripe Dashboard or
    # the portal) without chasing the org down for one later. Distinct
    # from PaymentMethod, which is per-agent and pays for gifts, not
    # the subscription itself. Display-only snapshot fields mirror
    # PaymentMethod's, same non-refetched convention.
    stripe_default_payment_method_id = db.Column(db.String(255), nullable=True)
    card_brand = db.Column(db.String(30), nullable=True)
    card_last4 = db.Column(db.String(4), nullable=True)
    card_exp_month = db.Column(db.Integer, nullable=True)
    card_exp_year = db.Column(db.Integer, nullable=True)

    def card_on_file_label(self):
        if not self.card_last4:
            return None
        brand = (self.card_brand or "Card").capitalize()
        return f"{brand} \u2022\u2022\u2022\u2022 {self.card_last4}"

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Global gift catalog curation: False (default) = this agency can send
    # any active global catalog item. True = only items in
    # OrgCatalogSelection are available, even if that ends up being zero.
    catalog_curated = db.Column(db.Boolean, default=False, nullable=False)

    # Free hand-delivery to this agency's office, in lieu of shipping/pickup.
    # Platform-admin-controlled per org -- reserved for pro/team agencies
    # close enough to drive to. office_address is set by the platform admin
    # (not the agency) since it's used to decide/display the drop-off
    # destination, not collected from the agency's own settings yet.
    dropoff_enabled = db.Column(db.Boolean, default=False, nullable=False)
    office_address = db.Column(db.String(255), nullable=True)

    # --- Outbound sender identity ---
    # One From address per org on our domain-authenticated sending
    # domain (see SENDGRID_SENDING_DOMAIN config) -- e.g.
    # "northstarrealty" -> northstarrealty@mail.andgifts.app. No
    # per-agent verification needed since the whole domain is
    # authenticated once; the individual agent's real email goes in
    # Reply-To instead (see send_flow_action_email), so client replies
    # still reach the agent even though the From address is shared
    # across the org. Auto-generated at org creation (see
    # generate_sender_local_part); admin-editable afterward in
    # settings, so it must stay globally unique across all orgs.
    sender_local_part = db.Column(db.String(64), unique=True, nullable=True)

    users = db.relationship("User", back_populates="org", cascade="all, delete-orphan")
    contacts = db.relationship("Contact", back_populates="org", cascade="all, delete-orphan")
    practice_type = db.relationship("PracticeType")

    @staticmethod
    def generate_sender_local_part(name):
        """Slugifies `name` into a candidate local-part and appends a
        short numeric suffix if it collides with an existing org --
        two brokerages can easily share a name. Call within the same
        transaction as the Org insert (after flush, so this org's own
        id doesn't collide with itself)."""
        base = slugify_sender_local_part(name)
        candidate = base
        suffix = 1
        while Org.query.filter_by(sender_local_part=candidate).first():
            suffix += 1
            candidate = f"{base}{suffix}"
        return candidate

    def sender_from(self):
        """(email, name) to use as the From on flow-action emails for
        any agent in this org. Falls back to the generic notifications
        address if this org somehow has no local-part set yet (e.g.
        an org created before this feature existed and not yet
        backfilled)."""
        from flask import current_app
        domain = current_app.config.get("SENDGRID_SENDING_DOMAIN")
        if not self.sender_local_part or not domain:
            return None, None
        return f"{self.sender_local_part}@{domain}", self.name

    def contact_count(self):
        return len(self.contacts)

    def limit_for(self, key):
        from flask import current_app
        return current_app.config["TIER_LIMITS"][self.tier][key]

    def display_tier_name(self):
        """Human-facing plan name (e.g. 'Solo') for the internal tier key
        (e.g. 'starter') -- PRICING_DISPLAY is the single source of truth
        for what a tier is CALLED, same way TIER_LIMITS is for what it
        GETS, so nothing should ever build this name by capitalizing
        self.tier directly (that's how 'Solo' regressed to 'Starter' in
        the account badge)."""
        from flask import current_app
        return current_app.config["PRICING_DISPLAY"][self.tier]["display_name"]

    def can_add_contact(self):
        limit = self.limit_for("contacts")
        return limit is None or self.contact_count() < limit

    def feature_enabled(self, key):
        return bool(self.limit_for(key))

    # --- Send limits (email/sms) ---
    # Cost + deliverability protection, not a revenue lever -- see the
    # comment above TIER_LIMITS in config.py for why these exist and
    # why they're separate from the (removed) per-tier channel gating.
    def _sends_this_month(self, action_type):
        from datetime import datetime as dt
        from app.models import ActionLog
        month_start = dt.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (
            ActionLog.query
            .filter_by(org_id=self.id, action_type=action_type, delivery_status="sent")
            .filter(ActionLog.sent_at >= month_start)
            .count()
        )

    def _contact_sent_to_recently(self, contact_id, cooldown_days):
        """True if this contact received ANY automated email/text within
        the cooldown window, regardless of which channel -- the point is
        protecting the person from being over-contacted, not rationing
        each channel separately. A cooldown of 0/None disables the check
        (team tier default; also lets a future per-org override of 0
        mean 'no cooldown' cleanly)."""
        if not cooldown_days:
            return False
        from datetime import datetime as dt, timedelta
        from app.models import ActionLog
        cutoff = dt.utcnow() - timedelta(days=cooldown_days)
        return (
            ActionLog.query
            .filter_by(org_id=self.id, contact_id=contact_id, delivery_status="sent")
            .filter(ActionLog.action_type.in_(("email", "text")))
            .filter(ActionLog.sent_at >= cutoff)
            .first()
        ) is not None

    def email_sends_this_month(self):
        """Public wrapper around _sends_this_month for use outside this
        model (e.g. settings/billing.html showing usage-vs-limit)."""
        return self._sends_this_month("email")

    def can_send_email_now(self, contact_id):
        """Returns (allowed, reason). reason is None when allowed, else a
        short human-readable string suitable for showing the agent
        directly (surfaced via ActionLog.delivery_error / a flash
        message) -- see dashboard.approve_action."""
        cooldown_days = self.limit_for("contact_cooldown_days")
        if self._contact_sent_to_recently(contact_id, cooldown_days):
            return False, f"This contact was already emailed or texted within the last {cooldown_days} days."

        cap = self.limit_for("email_monthly_cap")
        if cap is not None and self._sends_this_month("email") >= cap:
            return False, f"Monthly email limit reached for this plan ({cap}/month)."

        return True, None

    # --- Fulfillment: free office drop-off ---
    def can_offer_dropoff(self):
        """Pro/team tier is required in addition to the admin toggle -- an
        org downgrading out of pro should lose the option even if it was
        previously turned on, without the admin having to remember to flip
        it off manually."""
        return self.dropoff_enabled and self.tier in ("pro", "team")

    # --- Seats (sub-accounts) ---
    def seat_count(self):
        """Active + pending seats count against the plan limit."""
        return sum(1 for u in self.users if u.status in ("active", "pending"))

    def can_add_seat(self):
        limit = self.limit_for("seats")
        return limit is None or self.seat_count() < limit

    # --- Gift catalog curation ---
    def available_catalog_items(self):
        """Active global catalog items this org can currently send."""
        from app.models.gifting import GiftCatalogItem, OrgCatalogSelection
        query = GiftCatalogItem.query.filter_by(org_id=None, is_active=True)
        if self.catalog_curated:
            selected_ids = [
                s.gift_catalog_item_id
                for s in OrgCatalogSelection.query.filter_by(org_id=self.id).all()
            ]
            query = query.filter(GiftCatalogItem.id.in_(selected_ids))
        return query.order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name).all()

    def selected_item_ids(self):
        """IDs currently in this org's selection table (only meaningful
        when catalog_curated is True, but harmless to call regardless)."""
        from app.models.gifting import OrgCatalogSelection
        return {
            s.gift_catalog_item_id
            for s in OrgCatalogSelection.query.filter_by(org_id=self.id).all()
        }


class User(UserMixin, db.Model):
    """An agent (or admin) logging into the platform, scoped to one org."""
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False)

    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    role = db.Column(db.Enum("admin", "agent", name="user_role"), default="agent")
    # Platform-operator flag (you), separate from per-org admin. Org admins
    # can manage their own org's contacts/team/custom catalog items; only a
    # platform_admin can create/edit the *global* gift catalog shared by
    # every agency on &Gifts.
    platform_admin = db.Column(db.Boolean, default=False, nullable=False)
    photo_url = db.Column(db.String(500), nullable=True)

    # --- Sub-account lifecycle ---
    # active  = normal, can log in
    # pending = invited by email, hasn't set a password / accepted yet
    # disabled = admin revoked access, kept for history/attribution
    status = db.Column(
        db.Enum("active", "pending", "disabled", name="user_status"),
        default="active",
        nullable=False,
        index=True,
    )
    invite_token = db.Column(db.String(64), unique=True, nullable=True, index=True)
    invite_expires_at = db.Column(db.DateTime, nullable=True)
    invited_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)

    # --- Email verification ---
    # Defaults to True so every EXISTING creation path (team invite accept,
    # admin direct-add, the original migration backfill) stays exactly as
    # trusted as it is today. Only self-registration (auth.register)
    # explicitly sets this False, since that's the one path where nobody
    # else has vouched for the address yet.
    email_verified = db.Column(db.Boolean, default=True, nullable=False)
    email_verify_token = db.Column(db.String(64), unique=True, nullable=True, index=True)
    email_verify_expires_at = db.Column(db.DateTime, nullable=True)

    # --- Password reset ---
    reset_token = db.Column(db.String(64), unique=True, nullable=True, index=True)
    reset_expires_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # --- Saved payment methods (gift purchases, not subscription billing --
    # see Org.stripe_customer_id for that, a separate Stripe Customer) ---
    # Created lazily the first time this agent adds a card (see
    # services.payments.get_or_create_stripe_customer), not at signup.
    stripe_customer_id = db.Column(db.String(255), nullable=True)

    org = db.relationship("Org", back_populates="users")
    invited_by = db.relationship("User", remote_side=[id], foreign_keys=[invited_by_user_id])
    payment_methods = db.relationship(
        "PaymentMethod", back_populates="user", cascade="all, delete-orphan",
        order_by="PaymentMethod.created_at",
    )

    @property
    def default_payment_method(self):
        return next((pm for pm in self.payment_methods if pm.is_default), None)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password)

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.email

    @property
    def is_admin(self):
        return self.role == "admin"

    # Flask-Login: don't let pending (no-password-yet) or disabled users log
    # in, and don't let a self-registered user in until they've clicked
    # their verification link either.
    @property
    def is_active(self):
        return self.status == "active" and self.email_verified
