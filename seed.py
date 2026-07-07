"""
Seeds baseline reference data: common interests and global gift triggers
mapped to standard timeline events. Run once after your first migration:

    python seed.py
"""
from app import create_app
from app.extensions import db
from app.models import Interest, GiftCatalogItem, GiftTrigger

STARTER_INTERESTS = [
    "golf", "wine", "football", "family/kids", "cooking", "gardening",
    "fitness", "travel", "coffee", "outdoors",
]

# (event_type, interest_tag or None, gift name, price_cents, description, tags)
STARTER_GIFTS_AND_TRIGGERS = [
    ("closing", None, "Housewarming Gift Box", 7500, "Curated welcome-home box.", ""),
    ("closing", "wine", "Wine & Cheese Closing Gift", 8500, "A bottle + local cheese board.", "wine"),
    ("closing", "family/kids", "Family Game Night Box", 6500, "Board games + snacks for the new house.", "family/kids"),
    ("one_year_anniversary", None, "One Year Candle Set", 4500, "Simple, elegant anniversary keepsake.", ""),
    ("one_year_anniversary", "golf", "Golf Gift Box", 6500, "Sleeve of balls, tees, and a towel.", "golf"),
    ("six_month_anniversary", None, "Coffee Sampler", 3500, "Small thank-you-for-being-our-client gift.", "coffee"),
    ("wedding_anniversary", None, "Wine Duo Set", 5500, "Two glasses + a nice bottle.", "wine"),
    ("birthday", None, "Birthday Treat Box", 4000, "Assorted local treats.", ""),
]


def run():
    app = create_app("production")
    with app.app_context():
        for name in STARTER_INTERESTS:
            if not Interest.query.filter_by(name=name).first():
                db.session.add(Interest(name=name))
        db.session.flush()

        for event_type, interest_tag, gift_name, price_cents, desc, tags in STARTER_GIFTS_AND_TRIGGERS:
            gift = GiftCatalogItem.query.filter_by(name=gift_name, org_id=None).first()
            if not gift:
                gift = GiftCatalogItem(
                    org_id=None, name=gift_name, description=desc,
                    price_cents=price_cents, interest_tags=tags, is_active=True,
                )
                db.session.add(gift)
                db.session.flush()

            existing_trigger = GiftTrigger.query.filter_by(
                org_id=None, event_type=event_type, interest_tag=interest_tag
            ).first()
            if not existing_trigger:
                db.session.add(GiftTrigger(
                    org_id=None, event_type=event_type,
                    interest_tag=interest_tag, suggested_gift_id=gift.id,
                ))

        db.session.commit()
        print("Seed complete.")


if __name__ == "__main__":
    run()
