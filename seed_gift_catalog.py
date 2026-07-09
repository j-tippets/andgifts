"""
Seeds the global gift catalog (org_id=None) with the starter box lineup.
Safe to re-run -- skips any item that already exists by name at org_id=None.

    python seed_gift_catalog.py
"""
from app import create_app
from app.extensions import db
from app.models import GiftCatalogItem

# (name, price_cents, description/contents, interest_tags)
CATALOG_ITEMS = [
    ('Swedish Candy Box', 4900, '1 lb Swedish candy mix; gift note; premium box', 'candy, sweet, fun, simple, thank you'),
    ('Candy + Candle', 4900, '1/2 lb Swedish candy; small candle; gift note', 'candy, candle, cozy, thank you'),
    ('New Home Sweet Home', 4900, 'Swedish candy; small home sweet home item; gift note', 'closing, new home, housewarming, real estate'),
    ('Movie Night Mini', 4900, 'Swedish candy; microwave popcorn; movie candy; gift note', 'movie, family, kids, weekend, cozy'),
    ('Coffee Break Box', 4900, 'Local coffee or cold brew bottle; Swedish candy; biscotti or cookie', 'coffee, office, morning, work'),
    ('Thank You Treat Box', 4900, 'Swedish candy; chocolate bar; small snack; gift note', 'thank you, appreciation, simple'),
    ('Dog Lover Mini', 4900, 'Swedish candy for owner; premium dog treat; small dog toy', 'dog, pet, puppy, animal lover'),
    ('Dirty Soda Starter', 4900, 'Swedish candy; soda syrup or flavor; cup; straw', 'soda, Utah, fun, sweet'),
    ('Cozy Night In', 4900, 'Swedish candy; tea or cocoa; soft socks', 'cozy, winter, comfort, relax'),
    ('Birthday Candy Box', 4900, 'Swedish candy; birthday candle; confetti-style packaging', 'birthday, celebration, candy'),
    ('Candy + Candle + Flowers', 9900, 'Swedish candy; candle; small floral arrangement', 'flowers, candle, premium, thank you'),
    ('Golf Lovers Box', 9900, 'Swedish candy; golf balls; tees; towel; ball marker', 'golf, sports, men, country club'),
    ('Romcom Movie Lover', 9900, 'Swedish candy; popcorn; cozy blanket or socks; romcom card', 'movie, romcom, cozy, date night'),
    ('New Home Essentials', 9900, 'Swedish candy; candle; dish towel; room spray', 'housewarming, new home, closing'),
    ('The Hostess Box', 9900, 'Swedish candy; candle; cocktail napkins; serving snack', 'hostess, dinner party, entertaining'),
    ('Spa Night Box', 9900, 'Swedish candy; bath soak; candle; face mask', 'spa, self care, relaxation'),
    ('Family Game Night', 9900, 'Swedish candy; popcorn; card game; snacks', 'family, kids, game night'),
    ('BBQ Backyard Box', 9900, 'Swedish candy; BBQ rub; sauce; grilling towel', 'bbq, backyard, summer, grill'),
    ('Book Lover Box', 9900, 'Swedish candy; bookmark; cozy socks; tea or cocoa', 'books, reading, cozy'),
    ('Garden Lover Box', 9900, 'Swedish candy; seed packets; garden gloves; hand cream', 'garden, flowers, outdoors'),
    ('Local Utah Box', 9900, 'Swedish candy plus 2-3 locally made Utah treats or items', 'local, Utah, artisan, thank you'),
    ('Welcome Home Box', 9900, 'Swedish candy; small plant; candle; welcome-home note', 'closing, welcome, real estate'),
    ('Teacher Appreciation Box', 9900, 'Swedish candy; candle; notebook; pen set', 'teacher, school, appreciation'),
    ('Office Snack Box', 9900, 'Swedish candy; mixed snacks; chocolate; nuts', 'office, team, workplace, snacks'),
    ('Pickleball Box', 9900, 'Swedish candy; paddle grip or tape; balls; towel', 'pickleball, sports, active'),
    ('Premium Golf Box', 14900, 'Swedish candy; premium golf balls; towel; tees; divot tool', 'golf, premium, client, men'),
    ('Elevated Movie Night', 14900, 'Swedish candy; popcorn kit; blanket; candle; movie snacks', 'movie, cozy, family, date night'),
    ('Luxury Housewarming Box', 14900, 'Swedish candy; candle; olive oil; dish towel; room spray', 'housewarming, closing, home'),
    ('New Home Kitchen Box', 14900, 'Swedish candy; artisan olive oil; spice blend; tea towel', 'kitchen, cooking, new home'),
    ('Self-Care Sunday', 14900, 'Swedish candy; candle; bath soak; lotion; face mask', 'self care, spa, women, relax'),
    ('Date Night In', 14900, 'Swedish candy; candle; mocktail mixer; popcorn or snacks; card game', 'date night, couple, romantic'),
    ('Baby Welcome Box', 14900, 'Swedish candy for parents; baby blanket or book; candle', 'baby, new parents, family'),
    ('Dog Parent Deluxe', 14900, 'Swedish candy; dog treats; toy; leash accessory; candle', 'dog, pet, dog lover'),
    ('The Sweet & Savory Box', 14900, 'Swedish candy; crackers; jam; nuts; chocolate; candle', 'foodie, snacks, premium'),
    ('Fresh Start Box', 14900, 'Swedish candy; planner or notebook; candle; coffee; pen', 'new year, productivity, fresh start'),
    ('Closing Day Celebration', 14900, 'Swedish candy; candle; flowers; welcome home accent item', 'closing, real estate, home buyer'),
    ('Client Appreciation Box', 14900, 'Swedish candy; candle; premium snack; handwritten card', 'appreciation, client, thank you'),
    ('Premium Closing Gift', 19900, 'Swedish candy; flowers; candle; home decor item; handwritten note', 'closing, real estate, premium'),
    ('The Entertainer Box', 19900, 'Swedish candy; charcuterie board; napkins; jam; crackers', 'hosting, entertainer, kitchen'),
    ('Luxury Golf Gift', 19900, 'Swedish candy; premium golf balls; towel; divot tool; golf accessory', 'golf, luxury, sports'),
    ('Home Spa Basket', 19900, 'Swedish candy; luxury candle; bath soak; robe or towel; lotion', 'spa, relaxation, luxury'),
    ('Family Welcome Basket', 19900, 'Swedish candy; game; popcorn kit; candle; kids treats', 'family, housewarming, movie night'),
    ('Gourmet Kitchen Box', 19900, 'Swedish candy; olive oil; balsamic; spice blend; dish towel', 'foodie, kitchen, cooking'),
    ('Cozy Home Deluxe', 19900, 'Swedish candy; throw blanket; candle; room spray; cocoa or tea', 'cozy, home, winter'),
    ('New Home Bar Cart', 19900, 'Swedish candy; mocktail mixer; glasses or cups; cocktail napkins', 'bar cart, entertaining, housewarming'),
    ('Local Artisan Basket', 19900, 'Swedish candy; local candle; local food item; local decor or goods', 'local, Utah, handmade'),
    ('Garden & Porch Box', 19900, 'Swedish candy; planter; gloves; seed packets; outdoor candle', 'garden, porch, spring'),
    ('Signature &Gifts Basket', 24900, 'Swedish candy; flowers; candle; gourmet snacks; home item; handwritten note', 'signature, premium, luxury, client'),
    ('Luxury Closing Basket', 24900, 'Swedish candy; floral arrangement; premium candle; home decor; kitchen item', 'closing, luxury, real estate'),
    ('Executive Appreciation Box', 24900, 'Swedish candy; premium snacks; coffee; candle; desk item; handwritten note', 'executive, business, appreciation'),
    ('Ultimate Golf Basket', 24900, 'Swedish candy; premium golf balls; towel; divot tool; glove; snacks', 'golf, premium, sports'),
    ('Ultimate Movie Night Basket', 24900, 'Swedish candy; popcorn kit; blanket; candle; snacks; game', 'movie night, family, cozy'),
    ('New Home Entertaining Basket', 24900, 'Swedish candy; serving board; gourmet snacks; napkins; candle', 'housewarming, hosting, kitchen'),
    ('The Big Thank You', 24900, 'Swedish candy; flowers; candle; artisan treats; custom note', 'thank you, appreciation, premium'),
    ('Local Luxury Utah Basket', 24900, 'Swedish candy plus several premium local Utah-made products', 'Utah, local, artisan, premium'),
    ('Family Favorites Basket', 24900, 'Swedish candy; game; snacks; blanket; popcorn; kid-friendly treats', 'family, kids, housewarming'),
    ('Spa Retreat Basket', 24900, 'Swedish candy; luxury candle; bath products; robe or towel; tea; lotion', 'spa, luxury, self care'),]


def run():
    app = create_app("production")
    with app.app_context():
        added = 0
        for name, price_cents, description, tags in CATALOG_ITEMS:
            existing = GiftCatalogItem.query.filter_by(org_id=None, name=name).first()
            if existing:
                continue
            db.session.add(GiftCatalogItem(
                org_id=None,
                name=name,
                description=description,
                price_cents=price_cents,
                interest_tags=tags,
                item_type="product",
                is_active=True,
            ))
            added += 1
        db.session.commit()
        print(f"Seed complete. Added {added} new item(s), skipped {len(CATALOG_ITEMS) - added} already present.")


if __name__ == "__main__":
    run()
