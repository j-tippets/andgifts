from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class Order(db.Model):
    """
    A one-off gift purchase placed directly by an agent for a contact,
    outside the automated suggestion/campaign flow. Paid by charging the
    agent's saved card directly (see services/payments.charge_saved_card)
    at confirm time -- routes/orders.confirm_order sets status to "paid"
    synchronously right after a successful charge, since there's no
    Stripe Checkout redirect in this flow to wait on a webhook for.

    stripe_checkout_session_id is a holdover from the original Stripe-
    Checkout-based flow and stays unpopulated for orders created through
    the current flow; left in place since routes/orders.stripe_webhook's
    "mode == payment" branch is harmless dead code rather than something
    worth surgically removing from a shared webhook endpoint.
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
    # Same snapshot rationale as gift_name_snapshot/gift_price_cents above,
    # for the catalog bookkeeping fields WDF's fulfillment notice needs.
    # Nullable because a manually-placed item without these fields set (or
    # an order with no gift_catalog_item_id at all) simply has nothing to
    # snapshot.
    sku_snapshot = db.Column(db.String(50), nullable=True)
    occasion_snapshot = db.Column(db.String(100), nullable=True)
    recipe_id_snapshot = db.Column(db.String(20), nullable=True)

    # The note to go along with the gift, if any -- either a fixed
    # message the flow's builder wrote, or one the LLM generated (see
    # suggestion_engine._resolve_gift_note); blank for a plain gift
    # with no note attached, and always null for a manually-placed
    # one-off order (routes/orders.py has no note step). Carried onto
    # the order itself (rather than left on the originating
    # SuggestedAction) so WDF's fulfillment notice can show it
    # regardless of which flow produced the order.
    note_text = db.Column(db.Text, nullable=True)

    fulfillment_method = db.Column(
        db.Enum("shipping", "pickup", "dropoff", name="order_fulfillment_method"), nullable=False
    )
    pickup_location = db.Column(db.String(255), nullable=True)
    # Snapshot of org.office_address at order time, so this stays accurate
    # even if the office address changes or drop-off is later disabled.
    dropoff_location = db.Column(db.String(255), nullable=True)
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

    # Which saved card (see PaymentMethod) this was charged on -- set
    # once the agent picks a card during checkout, before the actual
    # charge happens. Nullable because it doesn't exist yet for the
    # brief window between order creation and payment-method selection.
    payment_method_id = db.Column(db.String(36), db.ForeignKey("payment_methods.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime, nullable=True)

    contact = db.relationship("Contact")
    gift_catalog_item = db.relationship("GiftCatalogItem")
    ordered_by = db.relationship("User", foreign_keys=[ordered_by_user_id])
    payment_method = db.relationship("PaymentMethod")

    @property
    def total_cents(self):
        return self.gift_price_cents + (self.shipping_cost_cents or 0)
