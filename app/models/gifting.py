from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class GiftCatalogItem(db.Model):
    """
    A sendable gift. org_id is nullable -- null means it's a global
    catalog item (curated by you, synced from Shopify); non-null means
    an org added a custom/private item.
    """
    __tablename__ = "gift_catalog_items"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=True, index=True)

    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_cents = db.Column(db.Integer, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)

    # "product" = a physical, shippable item (the common case today).
    # "service" = something redeemed/booked rather than shipped (e.g. a
    # gift card, an experience). No fulfillment automation differs yet --
    # this is a tag for future filtering/reporting.
    item_type = db.Column(
        db.Enum("product", "service", name="gift_item_type"),
        default="product", nullable=False,
    )

    # Loose CSV tag match against Interest.name for MVP simplicity;
    # can be normalized to a proper join table later if needed.
    interest_tags = db.Column(db.String(500), nullable=True)

    shopify_product_id = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def tag_list(self):
        return [t.strip() for t in (self.interest_tags or "").split(",") if t.strip()]


class GiftTrigger(db.Model):
    """
    Maps a timeline event_type (+ optional interest) to a suggested
    gift category / specific catalog item. This is what the nightly
    suggestion job joins against.
    """
    __tablename__ = "gift_triggers"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=True, index=True)  # null = global default

    event_type = db.Column(db.String(50), nullable=False, index=True)
    interest_tag = db.Column(db.String(100), nullable=True)  # optional refinement
    suggested_gift_id = db.Column(db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=True)
    default_reason_template = db.Column(
        db.String(500),
        default="{contact_name}'s {event_label} is coming up on {event_date}.",
    )

    suggested_gift = db.relationship("GiftCatalogItem")


class OrgCatalogSelection(db.Model):
    """
    Marks one global catalog item as included in a specific org's curated
    catalog. Only consulted when that Org's catalog_curated flag is True --
    see Org.available_catalog_items(). Agencies no longer add their own
    custom items; this is purely an allow-list over the global catalog.
    """
    __tablename__ = "org_catalog_selections"
    __table_args__ = (
        db.UniqueConstraint("org_id", "gift_catalog_item_id", name="uq_org_catalog_selection"),
    )

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)
    gift_catalog_item_id = db.Column(
        db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    gift_catalog_item = db.relationship("GiftCatalogItem")
