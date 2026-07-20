"""
QA seed script for &Gifts.

Wipes and rebuilds a self-contained "QA Test Agency" org, then runs both
suggestion engines (legacy GiftTrigger path + campaign/flow path) so a
full set of pending actions exists to manually approve and check.

What it builds:
  - 1 admin + 10 agents (agent #1 is the "primary" agent everything else
    hangs off of; agent #2 gets a small setup of their own so per-agent
    scoping on the Actions report has two agents to actually distinguish
    between, not just one)
  - 3 org-scope + 2 personal custom fields
  - 3 org-scope + 2 personal custom milestone (event) types
  - 7 flows on the primary agent, deliberately covering: every
    action_type (gift/email/text/handwritten_note), an interest_tag
    rule, a cooldown_days rule, a once_per_contact rule, a positive AND
    negative day offset, a recurring AND a one-time trigger event, an
    org-scope custom milestone trigger, and a personal custom milestone
    trigger
  - 10 contacts for the primary agent (one deliberately do_not_contact,
    one marketing_opt_out, one shared/unowned, one a clean control with
    no matching milestones at all) with timeline events dated so they
    land inside the engine's lookahead window right now -- backdated by
    exactly the right number of days for one-time triggers, and backdated
    by full years (same month/day) for recurring ones
  - Runs generate_suggestions_for_org + generate_campaign_suggestions_for_org

What it deliberately does NOT do: approve/skip/delete anything, or send
real email. That's the manual step -- log in, work the Today tab and the
Flows > Actions report, then run scripts/qa_report.py for a summary.

SAFETY: refuses to run against anything that isn't sqlite unless you
pass --i-am-sure. Never point this at a production database -- it does
a real DELETE of any prior "QA Test Agency" org before rebuilding.

Usage:
    python scripts/qa_seed.py
    python scripts/qa_seed.py --i-am-sure     # only if the DB isn't sqlite and you're SURE it's disposable
    QA_TEST_EMAIL=you@realaddress.com python scripts/qa_seed.py   # so the SendGrid test contact has a real inbox
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("DATABASE_URL") and os.environ.get("LOCAL_SQLITE_URI"):
    # config.py's SQLALCHEMY_ENGINE_OPTIONS hardcodes a MySQL-only SSL
    # connect_args (for DigitalOcean's managed MySQL) that SQLite's
    # DBAPI doesn't understand -- harmless in the real app since
    # production always uses DATABASE_URL (MySQL), but it makes
    # create_app() blow up here when running locally against sqlite.
    # Strip it before create_app() runs, only in that sqlite case.
    import config as _config
    _config.Config.SQLALCHEMY_ENGINE_OPTIONS = {}

from app import create_app
from app.extensions import db
from app.models import (
    Org, User, Contact, ContactPerson, ContactMethod, Interest,
    CustomFieldDefinition, CustomEventType,
    TimelineEvent,
    GiftCatalogItem, GiftTrigger,
    CampaignRecipe, Campaign, CampaignRule,
    SuggestedAction, ActionLog, ContactAuditLog, Order,
)
from app.services.suggestion_engine import generate_suggestions_for_org, generate_campaign_suggestions_for_org
from seed import STARTER_INTERESTS, STARTER_GIFTS_AND_TRIGGERS

ORG_NAME = "QA Test Agency"
PASSWORD = "QaTest123!"
TEST_EMAIL = os.environ.get("QA_TEST_EMAIL", "qa-test-contact@example.com")

TODAY = date.today()
TEST_DOMAIN = TEST_EMAIL.split("@", 1)[-1] if "@" in TEST_EMAIL else "example.com"


def _backdated_year(years_ago, month, day):
    """A date `years_ago` years back with the given month/day -- for
    recurring events, only month/day matters to the engine, so the year
    just needs to be believably in the past. Handles Feb 29 landing on
    a non-leap year the same way the engine's own occurrence math does."""
    year = TODAY.year - years_ago
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def guard_against_production(app):
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        if "--i-am-sure" not in sys.argv:
            print(f"Refusing to run: database does not look like sqlite ({uri.split('://')[0]}://...).")
            print("This script DELETES any existing 'QA Test Agency' org and rebuilds it.")
            print("If you are certain this is a disposable dev database, re-run with --i-am-sure.")
            sys.exit(1)
        print("!! --i-am-sure passed -- proceeding against a non-sqlite database !!")


def ensure_baseline_reference_data():
    """Idempotent: same logic as seed.py's run(), just inlined so we
    don't have to juggle a second app/app_context. Safe to call every
    time -- only creates rows that don't already exist, and never
    touches anything org-scoped."""
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

        if not GiftTrigger.query.filter_by(org_id=None, event_type=event_type, interest_tag=interest_tag).first():
            db.session.add(GiftTrigger(
                org_id=None, event_type=event_type, interest_tag=interest_tag, suggested_gift_id=gift.id,
            ))
    db.session.commit()


def wipe_existing_org():
    org = Org.query.filter_by(name=ORG_NAME).first()
    if not org:
        return
    print(f"Found existing '{ORG_NAME}' (id={org.id}) -- wiping it before rebuilding...")
    org_id = org.id
    # Dependency-safe order: children before parents. Raw SQL rather
    # than ORM cascades, since not every relationship here cascades and
    # getting the order right matters more than it being pretty.
    statements = [
        "DELETE FROM custom_field_values WHERE contact_id IN (SELECT id FROM contacts WHERE org_id = :org_id)",
        "DELETE FROM contact_methods WHERE person_id IN "
        "  (SELECT id FROM contact_people WHERE contact_id IN (SELECT id FROM contacts WHERE org_id = :org_id))",
        "DELETE FROM contact_people WHERE contact_id IN (SELECT id FROM contacts WHERE org_id = :org_id)",
        "DELETE FROM contact_interests WHERE contact_id IN (SELECT id FROM contacts WHERE org_id = :org_id)",
        "DELETE FROM timeline_events WHERE contact_id IN (SELECT id FROM contacts WHERE org_id = :org_id)",
        "DELETE FROM orders WHERE org_id = :org_id",
        "DELETE FROM action_log WHERE org_id = :org_id",
        "DELETE FROM contact_audit_log WHERE org_id = :org_id",
        "DELETE FROM suggested_actions WHERE org_id = :org_id",
        "DELETE FROM campaign_rules WHERE campaign_id IN (SELECT id FROM campaigns WHERE org_id = :org_id)",
        "DELETE FROM campaigns WHERE org_id = :org_id",
        "DELETE FROM campaign_recipe_rules WHERE recipe_id IN (SELECT id FROM campaign_recipes WHERE org_id = :org_id)",
        "DELETE FROM campaign_recipes WHERE org_id = :org_id",
        "DELETE FROM custom_field_definitions WHERE org_id = :org_id",
        "DELETE FROM custom_event_types WHERE org_id = :org_id",
        "DELETE FROM gift_triggers WHERE org_id = :org_id",
        "DELETE FROM org_catalog_selections WHERE org_id = :org_id",
        "DELETE FROM gift_catalog_items WHERE org_id = :org_id",
        "DELETE FROM contacts WHERE org_id = :org_id",
        "DELETE FROM users WHERE org_id = :org_id",
        "DELETE FROM orgs WHERE id = :org_id",
    ]
    for stmt in statements:
        db.session.execute(db.text(stmt), {"org_id": org_id})
    db.session.commit()
    print("Wipe complete.")


def make_contact(org, owner, household_name, first, last, email=None, phone=None,
                  do_not_contact=False, marketing_opt_out=False, interest_names=None):
    contact = Contact(
        org_id=org.id, owner_user_id=owner.id if owner else None,
        household_name=household_name, status="active",
        do_not_contact=do_not_contact, marketing_opt_out=marketing_opt_out,
    )
    db.session.add(contact)
    db.session.flush()

    person = ContactPerson(contact_id=contact.id, first_name=first, last_name=last, household_role="head")
    db.session.add(person)
    db.session.flush()

    if email:
        db.session.add(ContactMethod(person_id=person.id, method_type="email", subtype="personal", value=email, is_primary=True))
    if phone:
        db.session.add(ContactMethod(person_id=person.id, method_type="phone", subtype="mobile", value=phone, is_primary=True))

    for name in interest_names or []:
        interest = Interest.query.filter_by(name=name).first()
        if interest:
            contact.interests.append(interest)

    db.session.flush()
    return contact


def add_event(contact, event_type, event_date, is_recurring=False, label=None, notes=None):
    event = TimelineEvent(
        contact_id=contact.id, event_type=event_type, event_date=event_date,
        is_recurring=is_recurring, recurrence_rule="annual" if is_recurring else "none",
        label=label, notes=notes,
    )
    db.session.add(event)
    db.session.flush()
    return event


def run():
    app = create_app()
    guard_against_production(app)

    with app.app_context():
        ensure_baseline_reference_data()
        wipe_existing_org()

        print(f"\nCreating '{ORG_NAME}'...")
        org = Org(name=ORG_NAME, tier="pro")
        db.session.add(org)
        db.session.flush()

        # --- Admin + 10 agents ---
        admin = User(org_id=org.id, email="qa-admin@qatest.local", first_name="Ada", last_name="Admin", role="admin")
        admin.set_password(PASSWORD)
        db.session.add(admin)

        agents = []
        for i in range(1, 11):
            agent = User(
                org_id=org.id, email=f"qa-agent{i}@qatest.local",
                first_name=f"Agent{i}", last_name="Test", role="agent",
            )
            agent.set_password(PASSWORD)
            db.session.add(agent)
            agents.append(agent)
        db.session.flush()
        primary = agents[0]
        secondary = agents[1]
        print(f"  Admin: {admin.email}")
        print(f"  Agents: {', '.join(a.email for a in agents)}")
        print(f"  Primary test agent: {primary.email}")
        print(f"  Secondary agent (for per-agent scoping checks): {secondary.email}")
        print(f"  Password for everyone: {PASSWORD}")

        # --- Custom fields ---
        db.session.add(CustomFieldDefinition(org_id=org.id, scope="org", label="Referral source", field_type="text"))
        db.session.add(CustomFieldDefinition(
            org_id=org.id, scope="org", label="Client tier", field_type="select", options="Gold,Silver,Bronze",
        ))
        db.session.add(CustomFieldDefinition(org_id=org.id, scope="org", label="VIP", field_type="checkbox"))
        db.session.add(CustomFieldDefinition(
            org_id=org.id, scope="personal", owner_user_id=primary.id, label="Coffee order", field_type="text",
        ))
        db.session.add(CustomFieldDefinition(
            org_id=org.id, scope="personal", owner_user_id=primary.id, label="Preferred contact time",
            field_type="select", options="Morning,Afternoon,Evening",
        ))

        # --- Custom milestone (event) types ---
        org_milestone_5yr = CustomEventType(org_id=org.id, scope="org", key="5_year_anniversary", label="5 Year Anniversary")
        db.session.add(org_milestone_5yr)
        db.session.add(CustomEventType(org_id=org.id, scope="org", key="referral_given", label="Referral Given"))
        db.session.add(CustomEventType(org_id=org.id, scope="org", key="home_renovation", label="Home Renovation"))
        personal_milestone_kindergarten = CustomEventType(
            org_id=org.id, scope="personal", owner_user_id=primary.id,
            key="kid_started_kindergarten", label="Kid Started Kindergarten",
        )
        db.session.add(personal_milestone_kindergarten)
        db.session.add(CustomEventType(
            org_id=org.id, scope="personal", owner_user_id=primary.id,
            key="second_home_purchase", label="Second Home Purchase",
        ))
        db.session.flush()

        # --- Admin "imports" the entire global Flow Library: adds every
        # active global recipe as their own personal flow. (There's no
        # single bulk "for the whole company" action in the product --
        # every flow is personal, added one at a time via recipe_book's
        # "add" button -- so this simulates the admin doing that for
        # each recipe currently in the library.)
        global_recipes = CampaignRecipe.query.filter_by(org_id=None, is_active=True).all()
        for recipe in global_recipes:
            campaign = Campaign.from_recipe(recipe, org_id=org.id, owner_user_id=admin.id, created_by_user_id=admin.id)
            db.session.add(campaign)
        db.session.flush()
        print(f"\nAdmin imported {len(global_recipes)} flow(s) from the global Flow Library.")

        # --- Gift items to hang flows off (reuse the global starter catalog) ---
        housewarming = GiftCatalogItem.query.filter_by(name="Housewarming Gift Box", org_id=None).first()
        golf_box = GiftCatalogItem.query.filter_by(name="Golf Gift Box", org_id=None).first()
        wine_duo = GiftCatalogItem.query.filter_by(name="Wine Duo Set", org_id=None).first()

        # --- 7 flows on the primary agent, covering every action_type,
        # a recurring AND one-time trigger, positive AND negative
        # offsets, an interest_tag + cooldown_days + once_per_contact
        # rule, and both an org-scope and a personal custom milestone. ---

        flow_closing_gift = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="Purchasers - Closing Gift", event_type="closing", offset_days=0,
            action_type="gift", suggested_gift_id=housewarming.id if housewarming else None, is_active=True,
        )
        db.session.add(flow_closing_gift)

        flow_showing_followup = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="Post-Showing Follow-Up", event_type="showing", offset_days=2,
            action_type="text", use_llm_copy=False,
            message_template="Hi {contact_name}, how did you feel about the showing on {event_date}?",
            is_active=True,
        )
        db.session.add(flow_showing_followup)

        flow_six_month_email = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="6-Month Check-In Email", event_type="six_month_anniversary", offset_days=0,
            action_type="email", use_llm_copy=False,
            message_template="Hi {contact_name}, just checking in six months after your {event_label} -- how's everything going?",
            is_active=True,
        )
        db.session.add(flow_six_month_email)

        flow_anniversary_note = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="Anniversary Handwritten Note", event_type="one_year_anniversary", offset_days=0,
            action_type="handwritten_note", use_llm_copy=False,
            message_template="Happy one year anniversary, {contact_name}! Thank you for trusting us.",
            is_active=True,
        )
        db.session.add(flow_anniversary_note)
        db.session.flush()
        flow_anniversary_note.rules = [CampaignRule(rule_type="once_per_contact", config={})]

        flow_golf_wedding_gift = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="Golf Lovers Wedding Anniversary Gift", event_type="wedding_anniversary", offset_days=-3,
            action_type="gift", suggested_gift_id=golf_box.id if golf_box else None, is_active=True,
        )
        db.session.add(flow_golf_wedding_gift)
        db.session.flush()
        flow_golf_wedding_gift.rules = [
            CampaignRule(rule_type="interest_tag", config={"tag": "golf"}, position=0),
            CampaignRule(rule_type="cooldown_days", config={"days": 90}, position=1),
        ]

        flow_org_milestone_5yr = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="5 Year Anniversary Gift", event_type=org_milestone_5yr.key, offset_days=0,
            action_type="gift", suggested_gift_id=wine_duo.id if wine_duo else None, is_active=True,
        )
        db.session.add(flow_org_milestone_5yr)

        flow_personal_milestone = Campaign(
            org_id=org.id, owner_user_id=primary.id, created_by_user_id=primary.id,
            name="Kindergarten Check-In", event_type=personal_milestone_kindergarten.key, offset_days=0,
            action_type="text", use_llm_copy=False,
            message_template="Hi {contact_name}, hope the first week of kindergarten went well!",
            is_active=True,
        )
        db.session.add(flow_personal_milestone)
        db.session.flush()

        # --- Secondary agent: one small flow + contact, purely so the
        # admin's per-agent filter and the non-admin scoping rules have
        # a second agent's data to distinguish from the primary's. ---
        flow_secondary = Campaign(
            org_id=org.id, owner_user_id=secondary.id, created_by_user_id=secondary.id,
            name="Secondary Agent - Closing Gift", event_type="closing", offset_days=0,
            action_type="gift", suggested_gift_id=housewarming.id if housewarming else None, is_active=True,
        )
        db.session.add(flow_secondary)
        secondary_contact = make_contact(
            org, secondary, "The Secondary-Agent Test Household", "Sam", "Secondary",
            email=TEST_EMAIL, phone="555-010-9999",
        )
        add_event(secondary_contact, "closing", TODAY, is_recurring=False)

        db.session.flush()

        # --- 10 contacts for the primary agent ---

        c1_fresh_lead = make_contact(org, primary, "Doe - Fresh Lead", "Jamie", "Doe", email=f"jamie.doe.test@{TEST_DOMAIN}")
        add_event(c1_fresh_lead, "first_contact", TODAY, is_recurring=False)

        c2_showing = make_contact(org, primary, "Chen - Showing Scheduled", "Wei", "Chen", email=f"wei.chen.test@{TEST_DOMAIN}")
        add_event(c2_showing, "showing", TODAY - timedelta(days=2), is_recurring=False)

        c3_closing = make_contact(
            org, primary, "Patel - Closing Today", "Raj", "Patel", email=f"raj.patel.test@{TEST_DOMAIN}",
            interest_names=["wine"],
        )
        add_event(c3_closing, "closing", TODAY, is_recurring=False)

        c4_six_month = make_contact(
            org, primary, "Nguyen - Six Month Mark", "Linh", "Nguyen", email=TEST_EMAIL, phone="555-010-1004",
        )
        add_event(c4_six_month, "six_month_anniversary", TODAY, is_recurring=False)

        c5_one_year_plus_5yr = make_contact(
            org, primary, "Okafor - One Year + 5yr Milestone", "David", "Okafor", email=f"david.okafor.test@{TEST_DOMAIN}",
        )
        add_event(c5_one_year_plus_5yr, "one_year_anniversary", _backdated_year(3, TODAY.month, TODAY.day), is_recurring=True)
        add_event(c5_one_year_plus_5yr, org_milestone_5yr.key, TODAY, is_recurring=False, label="5 Year Anniversary")

        wedding_target = TODAY + timedelta(days=3)
        c6_wedding_golf = make_contact(
            org, primary, "Diallo - Wedding Anniversary (Golf)", "Amara", "Diallo", email=f"amara.diallo.test@{TEST_DOMAIN}",
            interest_names=["golf"],
        )
        add_event(
            c6_wedding_golf, "wedding_anniversary",
            _backdated_year(5, wedding_target.month, wedding_target.day), is_recurring=True,
        )

        birthday_target = TODAY + timedelta(days=10)
        c7_birthday = make_contact(org, primary, "Reyes - Birthday Soon", "Maria", "Reyes", email=f"maria.reyes.test@{TEST_DOMAIN}")
        add_event(
            c7_birthday, "birthday",
            _backdated_year(30, birthday_target.month, birthday_target.day), is_recurring=True,
        )

        c8_shared = make_contact(org, None, "Shared Household - No Owner", "Alex", "Shared", email=f"alex.shared.test@{TEST_DOMAIN}")
        add_event(c8_shared, "closing", TODAY, is_recurring=False)

        c9_do_not_contact = make_contact(
            org, primary, "Blocked - Do Not Contact", "Sam", "Blocked", email=f"sam.blocked.test@{TEST_DOMAIN}",
            do_not_contact=True,
        )
        add_event(c9_do_not_contact, "closing", TODAY, is_recurring=False)

        c10_opt_out_personal = make_contact(
            org, primary, "Kim - Marketing Opt-Out", "Jin", "Kim", email=f"jin.kim.test@{TEST_DOMAIN}",
            marketing_opt_out=True,
        )
        add_event(
            c10_opt_out_personal, personal_milestone_kindergarten.key, TODAY, is_recurring=False,
            label="Kid Started Kindergarten",
        )

        db.session.commit()
        print(f"\nCreated 10 contacts for {primary.email} (plus 1 for {secondary.email}).")

        # --- Run both suggestion engines, same as a real dashboard load ---
        print("\nRunning suggestion engines...")
        legacy_created = generate_suggestions_for_org(org, today=TODAY)
        campaign_created = generate_campaign_suggestions_for_org(org, today=TODAY)
        print(f"  Legacy GiftTrigger path: {len(legacy_created)} suggestion(s)")
        print(f"  Campaign/flow path: {len(campaign_created)} suggestion(s)")

        pending_count = SuggestedAction.query.filter_by(org_id=org.id, status="pending").count()
        dnc_pending = (
            SuggestedAction.query.filter_by(org_id=org.id, status="pending", contact_id=c9_do_not_contact.id).count()
        )
        print(f"\nTotal pending actions: {pending_count}")
        print(f"  Do-Not-Contact contact has {dnc_pending} pending action(s) (expect 0).")
        if dnc_pending:
            print("  !! Do-Not-Contact suppression looks broken -- investigate before trusting anything else. !!")

        print("\n" + "=" * 70)
        print("Seed complete. Next steps:")
        print(f"  1. Log in as {primary.email} / {PASSWORD} and work the Today tab")
        print(f"     (approve/skip/delete a mix -- there should be {pending_count} pending across the org).")
        print(f"  2. Log in as {admin.email} / {PASSWORD}, check Settings > Custom fields")
        print("     and Milestone events (org + personal), and Flows > Actions with the")
        print("     agent filter (try Everyone, the primary agent, the secondary agent,")
        print("     and Unassigned / shared).")
        print(f"  3. Approve the '6-Month Check-In Email' action for Nguyen to test the")
        print(f"     real SendGrid send (mail goes to {TEST_EMAIL} unless you set QA_TEST_EMAIL).")
        print("  4. Run scripts/qa_report.py for a full summary after you're done approving.")
        print("=" * 70)


if __name__ == "__main__":
    run()
