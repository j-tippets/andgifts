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
