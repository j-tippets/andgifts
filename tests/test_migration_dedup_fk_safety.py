"""
Regression test for a bug in the d42dce6fd934 migration's cleanup
step, found when actually running it against production: it deleted
duplicate suggested_actions rows without accounting for
ContactAuditLog's foreign key to them. Unlike ActionLog (only written
on approval), ContactAuditLog gets a "qualified for a suggestion" row
the moment ANY suggestion is created (_log_qualified), so virtually
every duplicate has one -- the DELETE hit MySQL error 1451, "Cannot
delete or update a parent row: a foreign key constraint fails".

This isolates the actual mechanism rather than reproducing the full
migration (which isn't practical to run standalone here: it targets
duplicate rows, but SuggestedAction's own model now includes the very
uniqueness constraint that migration adds, via Priority 1's model
change -- so a schema built from current models can't hold two
"duplicate" rows to begin with, which is itself a good sign). What
actually matters is proving: (1) deleting a suggested_action still
referenced by a ContactAuditLog row fails under real FK enforcement
(the bug), and (2) deleting the referencing ContactAuditLog row(s)
first, then the suggested_action, succeeds (the fix) -- exactly the
two-statement order the corrected migration now uses.

SQLite doesn't enforce foreign keys by default (which is exactly why
the original bug shipped without a test catching it) -- enabled here
explicitly to reproduce MySQL's real behavior.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Contact, ContactAuditLog, SuggestedAction

from tests.conftest import make_org_and_user


def enable_sqlite_fk_enforcement():
    """In-memory sqlite:// engines use a single persistent connection
    per thread (SQLAlchemy's SingletonThreadPool, chosen automatically
    for in-memory sqlite precisely so the DB doesn't vanish between
    checkouts), so issuing this once on the current session is enough
    for it to stick for the rest of the test."""
    db.session.execute(text("PRAGMA foreign_keys=ON"))


def make_suggested_action_with_audit_log(org, user):
    contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Dupes")
    db.session.add(contact)
    db.session.flush()

    action = SuggestedAction(
        org_id=org.id, contact_id=contact.id, source_campaign_id=None,
        action_type="gift", reason_text="Test reason",
        target_date=date.today() + timedelta(days=5), status="pending",
    )
    db.session.add(action)
    db.session.flush()

    # The "qualified for a suggestion" audit row every real suggestion
    # gets on creation -- see _log_qualified. This is what the
    # original migration didn't account for.
    log = ContactAuditLog(
        org_id=org.id, contact_id=contact.id,
        contact_name_snapshot=contact.household_name,
        actor_name_snapshot="System", action="action_suggested",
        summary="Qualified for a suggested gift.",
        suggested_action_id=action.id,
    )
    db.session.add(log)
    db.session.commit()
    return action.id


def test_deleting_suggested_action_before_its_audit_log_fails(app, db):
    """Reproduces the exact production bug: deleting the parent row
    while a ContactAuditLog child still references it."""
    with app.app_context():
        enable_sqlite_fk_enforcement()
        org, user = make_org_and_user(db)
        action_id = make_suggested_action_with_audit_log(org, user)

        with pytest.raises(IntegrityError):
            db.session.execute(
                text("DELETE FROM suggested_actions WHERE id = :id"), {"id": action_id}
            )
            db.session.flush()
        db.session.rollback()


def test_deleting_audit_log_first_then_suggested_action_succeeds(app, db):
    """The fix: delete the referencing ContactAuditLog row(s) first,
    then the suggested_action -- exactly the order the corrected
    migration now uses."""
    with app.app_context():
        enable_sqlite_fk_enforcement()
        org, user = make_org_and_user(db)
        action_id = make_suggested_action_with_audit_log(org, user)

        db.session.execute(
            text("DELETE FROM contact_audit_log WHERE suggested_action_id = :id"),
            {"id": action_id},
        )
        db.session.execute(
            text("DELETE FROM suggested_actions WHERE id = :id"), {"id": action_id}
        )
        db.session.commit()

        assert SuggestedAction.query.get(action_id) is None
        assert ContactAuditLog.query.filter_by(suggested_action_id=action_id).count() == 0


# --- Victim-selection SQL logic, tested in full isolation ---
#
# SuggestedAction's model now carries the very uniqueness constraint
# this migration adds (Priority 1's model change), so a schema built
# from the app's own models can never hold two colliding rows to test
# against -- itself a good sign, but it means the migration's actual
# victim-selection queries need their own standalone tables (just the
# columns they touch) to exercise against a genuine "before the fix"
# duplicate scenario.

import sqlite3


def _build_isolated_schema(conn):
    conn.executescript("""
        CREATE TABLE suggested_actions (
            id TEXT PRIMARY KEY,
            org_id TEXT, source_campaign_id TEXT, contact_id TEXT,
            triggering_event_id TEXT, target_date TEXT,
            action_type TEXT, created_at TEXT
        );
        CREATE TABLE action_log (
            id TEXT PRIMARY KEY,
            suggested_action_id TEXT,
            cost_cents INTEGER
        );
        CREATE TABLE contact_audit_log (
            id TEXT PRIMARY KEY,
            suggested_action_id TEXT
        );
    """)


UNSENT_VICTIMS_SQL = """
    SELECT t1.id FROM suggested_actions t1
    INNER JOIN suggested_actions t2
      ON t1.org_id = t2.org_id
      AND t1.source_campaign_id = t2.source_campaign_id
      AND t1.contact_id = t2.contact_id
      AND t1.triggering_event_id = t2.triggering_event_id
      AND t1.target_date = t2.target_date
    WHERE t1.source_campaign_id IS NOT NULL
      AND t1.triggering_event_id IS NOT NULL
      AND (t1.created_at > t2.created_at
           OR (t1.created_at = t2.created_at AND t1.id > t2.id))
      AND NOT EXISTS (
          SELECT 1 FROM action_log WHERE action_log.suggested_action_id = t1.id
      )
"""

SENT_NON_PAID_VICTIMS_SQL = """
    SELECT t1.id FROM suggested_actions t1
    INNER JOIN suggested_actions t2
      ON t1.org_id = t2.org_id
      AND t1.source_campaign_id = t2.source_campaign_id
      AND t1.contact_id = t2.contact_id
      AND t1.triggering_event_id = t2.triggering_event_id
      AND t1.target_date = t2.target_date
    WHERE t1.source_campaign_id IS NOT NULL
      AND t1.triggering_event_id IS NOT NULL
      AND t1.action_type NOT IN ('gift', 'handwritten_note')
      AND (t1.created_at > t2.created_at
           OR (t1.created_at = t2.created_at AND t1.id > t2.id))
      AND EXISTS (
          SELECT 1 FROM action_log WHERE action_log.suggested_action_id = t1.id
      )
"""


def test_approved_email_duplicate_is_resolved_automatically():
    """Reproduces the exact production case: two 'email' SuggestedAction
    rows for the same campaign+contact+event+date, BOTH approved (each
    has an ActionLog, cost_cents NULL since email never charges a
    card). The later one should be selected as a victim, its
    ActionLog's suggested_action_id nulled (not deleted -- the send
    itself really happened), and it should be fully removable."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=OFF")  # isolate the SELECT logic; FK behavior covered above
    _build_isolated_schema(conn)

    conn.execute(
        "INSERT INTO suggested_actions VALUES ('early', 'org1', 'camp1', 'contact1', 'event1', '2026-08-20', 'email', '2026-08-20 23:30:26')"
    )
    conn.execute(
        "INSERT INTO suggested_actions VALUES ('late', 'org1', 'camp1', 'contact1', 'event1', '2026-08-20', 'email', '2026-08-20 23:30:26')"
    )
    conn.execute("INSERT INTO action_log VALUES ('log-early', 'early', NULL)")
    conn.execute("INSERT INTO action_log VALUES ('log-late', 'late', NULL)")
    conn.commit()

    unsent = [r[0] for r in conn.execute(UNSENT_VICTIMS_SQL).fetchall()]
    sent_non_paid = [r[0] for r in conn.execute(SENT_NON_PAID_VICTIMS_SQL).fetchall()]

    assert unsent == []
    assert sent_non_paid == ["late"]

    conn.execute("UPDATE action_log SET suggested_action_id = NULL WHERE suggested_action_id = 'late'")
    conn.execute("DELETE FROM contact_audit_log WHERE suggested_action_id = 'late'")
    conn.execute("DELETE FROM suggested_actions WHERE id = 'late'")
    conn.commit()

    remaining = [r[0] for r in conn.execute("SELECT id FROM suggested_actions").fetchall()]
    assert remaining == ["early"]
    log_row = conn.execute("SELECT suggested_action_id FROM action_log WHERE id = 'log-late'").fetchone()
    assert log_row[0] is None, "the ActionLog row itself must survive, just detached"

    conn.close()


def test_approved_gift_duplicate_is_left_completely_untouched():
    """The financially-significant case: two 'gift' SuggestedAction
    rows, both approved (both have an ActionLog with real cost_cents).
    Neither query should select either one as a victim -- this is
    exactly the scenario that must surface as a loud constraint
    failure for manual review, not get silently resolved."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=OFF")
    _build_isolated_schema(conn)

    conn.execute(
        "INSERT INTO suggested_actions VALUES ('gift-early', 'org1', 'camp1', 'contact1', 'event1', '2026-08-20', 'gift', '2026-08-20 23:30:26')"
    )
    conn.execute(
        "INSERT INTO suggested_actions VALUES ('gift-late', 'org1', 'camp1', 'contact1', 'event1', '2026-08-20', 'gift', '2026-08-20 23:30:27')"
    )
    conn.execute("INSERT INTO action_log VALUES ('glog-early', 'gift-early', 24900)")
    conn.execute("INSERT INTO action_log VALUES ('glog-late', 'gift-late', 24900)")
    conn.commit()

    unsent = [r[0] for r in conn.execute(UNSENT_VICTIMS_SQL).fetchall()]
    sent_non_paid = [r[0] for r in conn.execute(SENT_NON_PAID_VICTIMS_SQL).fetchall()]

    assert unsent == []
    assert sent_non_paid == [], "an approved gift duplicate must never be auto-resolved"

    conn.close()
