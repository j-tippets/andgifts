from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class PaymentMethod(db.Model):
    """
    A saved card on an agent's Stripe Customer (see User.stripe_customer_id),
    used to pay for gifts -- both the manual one-off order flow and
    automated flow-triggered approvals. Deliberately per-agent, not per-org
    (see routes/settings.py and services/payments.py): each agent adds
    their own card in their own Settings page, same convention as personal
    custom fields/milestones.

    is_default marks which card automated flow approvals charge without
    asking -- there's no "pick a card" moment when an agent just clicks
    Approve on a suggestion, so exactly one card per user must be the
    unambiguous default. Adding a user's first card auto-sets it default;
    every later add leaves the existing default alone unless the agent
    explicitly changes it (see services/payments.set_default_payment_method).
    """
    __tablename__ = "payment_methods"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)

    stripe_payment_method_id = db.Column(db.String(255), nullable=False, unique=True)

    # Display-only snapshot from Stripe at save time -- never re-fetched,
    # so if the underlying card details ever change on Stripe's side
    # (they don't, for card brand/last4) this would just go stale rather
    # than break anything.
    card_brand = db.Column(db.String(30), nullable=True)
    card_last4 = db.Column(db.String(4), nullable=True)
    card_exp_month = db.Column(db.Integer, nullable=True)
    card_exp_year = db.Column(db.Integer, nullable=True)

    is_default = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="payment_methods")

    def display_label(self):
        brand = (self.card_brand or "Card").capitalize()
        return f"{brand} \u2022\u2022\u2022\u2022 {self.card_last4}" if self.card_last4 else brand
