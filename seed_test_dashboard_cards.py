"""
Seeds a batch of fake contacts + pending SuggestedAction cards so you can
click through every card type (gift, email, text, handwritten_note) on the
dashboard without waiting on real timeline events.

Safe to re-run: seeded contacts are tagged internally, so re-running just
adds another batch (harmless, if a little repetitive) and --clear only ever
removes rows carrying that tag -- it will never touch your real data.

Usage:
    python seed_test_dashboard_cards.py                        # seeds into the first org found
    python seed_test_dashboard_cards.py --org-email you@x.com  # seeds into a specific org (looked up by user email)
    python seed_test_dashboard_cards.py --clear                # removes all previously seeded test data
    python seed_test_dashboard_cards.py --org-email you@x.com --clear
"""
import argparse
from datetime import date, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Org, User, Contact, ContactPerson, ContactMethod,
    GiftCatalogItem, SuggestedAction, ActionLog,
)

SEED_TAG = "[SEED:dashboard-test]"

# Gift-card style catalog items to seed (org-scoped, so --clear can remove
# them cleanly without touching your real global catalog).
SEED_GIFT_CARDS = [
    ("$50 Amazon Gift Card", 5000, "Digital gift card, emailed to the recipient."),
    ("$25 Local Coffee Shop Gift Card", 2500, "Physical card, mailed or hand-delivered."),
    ("$75 Steakhouse Gift Card", 7500, "Digital gift card for a nicer thank-you."),
]

# (household_name, head_first, head_last, email, phone, [ (action_type, day_offset, reason_text, generated_message, gift_name_or_None) ])
CONTACTS_AND_ACTIONS = [
    (
        "The Whitfields", "Sarah", "Whitfield", "sarah.whitfield.test@example.com", "555-010-1001",
        [
            ("gift", 2, "Their closing anniversary is in 2 days.", None, "$50 Amazon Gift Card"),
        ],
    ),
    (
        "The Marlowes", "Tom", "Marlowe", "tom.marlowe.test@example.com", "555-010-1002",
        [
            ("gift", 5, "One-year anniversary of their closing next week.", None, "Golf Lovers Box"),
        ],
    ),
    (
        "The Chens", "Lisa", "Chen", "lisa.chen.test@example.com", "555-010-1003",
        [
            ("gift", 9, "Referred the Nguyens last month -- a thank-you is overdue.", None, "$25 Local Coffee Shop Gift Card"),
        ],
    ),
    (
        "The Okafors", "David", "Okafor", "david.okafor.test@example.com", "555-010-1004",
        [
            (
                "email", 3, "Six-month check-in since their closing.",
                "Hi David, just wanted to check in and see how you and the family are settling into the new place! "
                "Let me know if anything's come up or if you need a recommendation for a good local contractor. "
                "Always happy to help.",
                None,
            ),
        ],
    ),
    (
        "The Petrovas", "Elena", "Petrova", "elena.petrova.test@example.com", "555-010-1005",
        [
            (
                "email", 11, "Birthday coming up next week.",
                "Happy almost-birthday, Elena! Hope you've got something fun planned. "
                "Thinking of you and wanted to say thanks again for trusting me with the sale last year.",
                None,
            ),
        ],
    ),
    (
        "The Hendersons", "Mark", "Henderson", "mark.henderson.test@example.com", "555-010-1006",
        [
            ("text", 1, "Home inspection follow-up is due tomorrow.", "Hi Mark! Just confirming -- inspection's set for tomorrow at 10am. Reply here if that time still works!", None),
        ],
    ),
    (
        "The Ashfords", "Priya", "Ashford", "priya.ashford.test@example.com", "555-010-1007",
        [
            ("text", 4, "Closing is this week -- quick heads up text.", "Hi Priya, exciting week ahead! Closing is set for Thursday at 2pm. Let me know if you need anything before then.", None),
        ],
    ),
    (
        "The Delgados", "Carlos", "Delgado", "carlos.delgado.test@example.com", "555-010-1008",
        [
            ("handwritten_note", 7, "Wrapping up a long, tricky negotiation -- a personal note feels right.",
             "Carlos, thank you for your patience through all the back-and-forth on this one. "
             "It was a pleasure getting your family into a home you're excited about. Enjoy it!", None),
        ],
    ),
    (
        "The Fontaines", "Renee", "Fontaine", "renee.fontaine.test@example.com", "555-010-1009",
        [
            ("handwritten_note", 13, "Two-year anniversary of their closing.",
             "Happy home-aversary, Renee! Hard to believe it's been two years already. "
             "Hope the house is still treating you well -- always here if you need anything.", None),
        ],
    ),
    (
        "The Kowalskis", "Anna", "Kowalski", "anna.kowalski.test@example.com", "555-010-1010",
        [
            ("gift", 6, "Just closed on their first home.", None, "$75 Steakhouse Gift Card"),
            ("text", 8, "Move-in week check-in.", "Hi Anna! How's the unpacking going? Let me know if you need any mover or handyman recommendations!", None),
        ],
    ),
]


def _resolve_org(org_email):
    if org_email:
        user = User.query.filter_by(email=org_email).first()
        if not user:
            raise SystemExit(f"No user found with email {org_email!r}. Check the address and try again.")
        return user.org
    org = Org.query.order_by(Org.created_at).first()
    if not org:
        raise SystemExit("No org found in this database -- create your account first, then re-run this script.")
    return org


def _seed(org):
    print(f"Seeding test dashboard cards into org: {org.name!r} ({org.id})")

    # Gift-card catalog items, scoped to this org so cleanup is trivial.
    gift_cards_by_name = {}
    for name, price_cents, desc in SEED_GIFT_CARDS:
        item = GiftCatalogItem.query.filter_by(org_id=org.id, name=name).first()
        if not item:
            item = GiftCatalogItem(
                org_id=org.id, name=name, description=f"{desc} {SEED_TAG}",
                price_cents=price_cents, item_type="service", is_active=True,
            )
            db.session.add(item)
            db.session.flush()
        gift_cards_by_name[name] = item

    # Also grab a couple of existing global catalog items (e.g. from
    # seed_gift_catalog.py) to use for the non-gift-card gift examples.
    existing_gifts_by_name = {
        g.name: g for g in GiftCatalogItem.query.filter_by(org_id=None).all()
    }

    today = date.today()
    created_contacts = 0
    created_actions = 0

    for household_name, first, last, email, phone, actions in CONTACTS_AND_ACTIONS:
        tagged_name = f"[Test] {household_name}"
        contact = Contact.query.filter_by(org_id=org.id, household_name=tagged_name).first()
        if not contact:
            contact = Contact(
                org_id=org.id,
                household_name=tagged_name,
                status="active",
                notes=SEED_TAG,
            )
            db.session.add(contact)
            db.session.flush()

            person = ContactPerson(contact_id=contact.id, first_name=first, last_name=last, household_role="head")
            db.session.add(person)
            db.session.flush()

            db.session.add(ContactMethod(person_id=person.id, method_type="email", subtype="personal", value=email, is_primary=True))
            db.session.add(ContactMethod(person_id=person.id, method_type="phone", subtype="mobile", value=phone, is_primary=True))
            created_contacts += 1

        for action_type, day_offset, reason_text, generated_message, gift_name in actions:
            gift = None
            if gift_name:
                gift = gift_cards_by_name.get(gift_name) or existing_gifts_by_name.get(gift_name)
                if gift is None:
                    print(f"  (skipping unknown gift {gift_name!r} for {household_name})")

            db.session.add(SuggestedAction(
                org_id=org.id,
                contact_id=contact.id,
                action_type=action_type,
                suggested_gift_id=gift.id if gift else None,
                reason_text=reason_text,
                generated_message=generated_message,
                target_date=today + timedelta(days=day_offset),
                status="pending",
            ))
            created_actions += 1

    db.session.commit()
    print(f"Done. Created {created_contacts} new test contacts and {created_actions} pending cards.")
    print("Head to /dashboard to see them.")


def _clear(org):
    contacts = Contact.query.filter_by(org_id=org.id).filter(Contact.notes == SEED_TAG).all()
    contact_ids = [c.id for c in contacts]

    if not contact_ids:
        print("No seeded test contacts found for this org -- nothing to clear.")
    else:
        SuggestedAction.query.filter(SuggestedAction.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        ActionLog.query.filter(ActionLog.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        for c in contacts:
            db.session.delete(c)  # cascades to ContactPerson/ContactMethod/TimelineEvent
        print(f"Removed {len(contact_ids)} test contacts and their cards/history.")

    deleted_items = GiftCatalogItem.query.filter_by(org_id=org.id).filter(
        GiftCatalogItem.description.like(f"%{SEED_TAG}%")
    ).delete(synchronize_session=False)
    if deleted_items:
        print(f"Removed {deleted_items} seeded gift-card catalog items.")

    db.session.commit()


def run(org_email=None, clear=False):
    app = create_app("production")
    with app.app_context():
        org = _resolve_org(org_email)
        if clear:
            _clear(org)
        else:
            _seed(org)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-email", default=None, help="Email of a user in the org you want to seed/clear. Defaults to the first org in the database.")
    parser.add_argument("--clear", action="store_true", help="Remove previously seeded test data instead of creating more.")
    args = parser.parse_args()
    run(org_email=args.org_email, clear=args.clear)
