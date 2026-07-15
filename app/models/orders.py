from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class Order(db.Model):
    """
    A one-off gift purchase placed directly by an agent for a contact,
    outside the automated suggestion/campaign flow. Paid via Stripe
    Checkout (hosted). Status is driven by the checkout.session.completed
    webhook, not by the success-page redirect -- a browser landing on the
    success URL is not proof of payment, only the webhook (with Stripe's
    signature) is.
    """
    __tablename__ = "orders"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)
    contact_id = db.Column(db.String(36), db.ForeignKey("contacts.id"), nullable=False, index=True)
    ordered_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)

    gift_catalog_item_id = db.Column(db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=True)
    # Snapshots so the order stays accurate even if the catalog item's
    # name/price changes later, or the item itself is deleted.
    gift_name_snapshot = db.Column(db.String(255), nullable=False)
    gift_price_cents = db.Column(db.Integer, nullable=False)

    fulfillment_method = db.Column(
        db.Enum("shipping", "pickup", name="order_fulfillment_method"), nullable=False
    )
    pickup_location = db.Column(db.String(255), nullable=True)
    shipping_cost_cents = db.Column(db.Integer, default=0, nullable=False)
    # Populated from Stripe's own shipping_details once paid -- we don't
    # build a custom address form since Stripe Checkout collects it for us.
    shipping_address_snapshot = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.Enum("pending", "paid", "fulfilled", "cancelled", name="order_status"),
        default="pending", nullable=False, index=True,
    )

    stripe_checkout_session_id = db.Column(db.String(255), nullable=True, index=True)
    stripe_payment_intent_id = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime, nullable=True)

    contact = db.relationship("Contact")
    gift_catalog_item = db.relationship("GiftCatalogItem")
    ordered_by = db.relationship("User")

    @property
    def total_cents(self):
        return self.gift_price_cents + (self.shipping_cost_cents or 0)
