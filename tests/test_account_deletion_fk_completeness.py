"""Reproduces the self-service account deletion failure (DB-2).

`delete_org_completely` deletes org-scoped rows in a careful,
dependency-ordered sequence -- but the sequence omits six tables that
hold live foreign keys into `users` and `orgs`:

    payment_methods.user_id     -> users.id
    flow_recommendations.user_id-> users.id
    filler_action_states.user_id-> users.id
    milestone_priorities.user_id-> users.id
    org_event_log.org_id        -> orgs.id   (nullable, but a populated
                                              nullable FK still blocks
                                              the parent delete)
    contact_interests.contact_id-> contacts.id

Under SQLite with `PRAGMA foreign_keys=OFF` -- which is what this suite
ran under until conftest turned it on -- those orphans are silently
accepted and deletion appears to work. Under MySQL/InnoDB, which is
production, `DELETE FROM users WHERE org_id = ?` raises IntegrityError
and the customer gets a 500 on the Delete Account button.

That button is not optional: App Store guideline 5.1.1(v) requires
working in-app account deletion for any app that supports account
creation, and it is a GDPR/CCPA erasure obligation independently of
Apple.

The org shape built below is not an edge case -- it is the single most
common real shape of a Solo org: one user who has added a card.

These tests are marked xfail(strict=True) deliberately. They document
a known-broken behaviour without turning the suite red today, and the
moment the deletion sequence is fixed they will XPASS, which strict
mode reports as a failure telling you to delete the marker. They are
the acceptance criteria for that fix, not a permanent fixture.
"""
import pytest
from sqlalchemy import text

from app.models import (
    Contact,
    FillerActionState,
    FlowRecommendation,
    MilestonePriority,
    OrgEventLog,
    PaymentMethod,
)
from app.services.account_deletion import delete_org_completely
from tests.conftest import make_org_and_user


def _build_fully_populated_solo_org(db):
    """A sole-user org carrying one row in each of the tables the
    deletion sequence currently misses."""
    org, user = make_org_and_user(db, tier="starter", onboarding_step="done")

    contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Clients")
    db.session.add(contact)
    db.session.flush()

    db.session.add(PaymentMethod(
        user_id=user.id,
        stripe_payment_method_id="pm_test_deletion",
        card_brand="visa",
        card_last4="4242",
        is_default=True,
    ))
    db.session.add(FlowRecommendation(
        org_id=org.id, user_id=user.id,
        event_type="birthday", event_label="Birthday", contact_count=1,
    ))
    db.session.add(FillerActionState(
        org_id=org.id, user_id=user.id,
        filler_key="add_first_contact", status="dismissed",
    ))
    db.session.add(MilestonePriority(
        user_id=user.id, event_type="birthday", priority=70,
    ))
    db.session.add(OrgEventLog(
        org_id=org.id, org_name_snapshot=org.name,
        event_type="signup", to_tier="starter",
    ))
    db.session.commit()
    return org, user, contact


@pytest.mark.xfail(
    strict=True,
    reason="DB-2: delete_org_completely omits payment_methods, "
           "flow_recommendations, filler_action_states, milestone_priorities "
           "and org_event_log. Remove this marker once the sequence is fixed.",
)
def test_delete_org_completely_succeeds_for_a_solo_org_with_a_saved_card(app, db):
    org, _user, _contact = _build_fully_populated_solo_org(db)

    delete_org_completely(org)


@pytest.mark.xfail(
    strict=True,
    reason="DB-2: blocked by the same missing tables as the test above. "
           "This is the assertion that actually matters -- deletion must "
           "leave nothing behind, not merely not raise.",
)
def test_account_deletion_leaves_no_orphaned_rows(app, db):
    """The real deliverable. A deletion that runs without raising but
    strands a chargeable card or a user row is a worse outcome than a
    500, because nobody finds out."""
    org, user, _contact = _build_fully_populated_solo_org(db)
    org_id, user_id = org.id, user.id

    delete_org_completely(org)

    orphan_counts = {
        "users": db.session.execute(
            text("SELECT COUNT(*) FROM users WHERE org_id = :o"), {"o": org_id}
        ).scalar(),
        "orgs": db.session.execute(
            text("SELECT COUNT(*) FROM orgs WHERE id = :o"), {"o": org_id}
        ).scalar(),
        "payment_methods": db.session.execute(
            text("SELECT COUNT(*) FROM payment_methods WHERE user_id = :u"), {"u": user_id}
        ).scalar(),
        "flow_recommendations": db.session.execute(
            text("SELECT COUNT(*) FROM flow_recommendations WHERE user_id = :u"), {"u": user_id}
        ).scalar(),
        "filler_action_states": db.session.execute(
            text("SELECT COUNT(*) FROM filler_action_states WHERE user_id = :u"), {"u": user_id}
        ).scalar(),
        "milestone_priorities": db.session.execute(
            text("SELECT COUNT(*) FROM milestone_priorities WHERE user_id = :u"), {"u": user_id}
        ).scalar(),
        "contacts": db.session.execute(
            text("SELECT COUNT(*) FROM contacts WHERE org_id = :o"), {"o": org_id}
        ).scalar(),
    }

    assert orphan_counts == {k: 0 for k in orphan_counts}, orphan_counts

    # org_event_log is deliberately NOT in the list above. Its org_id is
    # nullable and it carries org_name_snapshot precisely so the
    # platform-admin activity history survives the org -- so the correct
    # fix is to NULL org_id here, not to delete the row.
    surviving_history = db.session.execute(
        text("SELECT COUNT(*) FROM org_event_log WHERE org_id IS NULL")
    ).scalar()
    assert surviving_history == 1, "org lifecycle history should outlive the org"
