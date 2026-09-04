"""
Regression tests for reconcile_stuck_processing_actions (jobs/
reconcile_stuck_actions.py) -- the backstop for Priority 1's known
residual gap: a hard process crash between claiming a SuggestedAction
for approval and either charging succeeding or failing leaves it
stuck at "processing" forever, invisible on the dashboard (which only
ever queries status == "pending").

Covers:
- A "processing" row older than the staleness threshold is released
  back to "pending" with processing_started_at cleared.
- A "processing" row younger than the threshold (a normal, currently
  in-flight claim -- e.g. mid-Stripe-call) is left alone.
- Non-"processing" rows are never touched, regardless of age.
- The returned summary describes what was released, using values
  captured before the reconciling update (not the live ORM objects,
  which would show the post-reset values after commit -- see the
  function's docstring for why).
"""
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import Contact, SuggestedAction
from app.services.suggestion_engine import reconcile_stuck_processing_actions

from tests.conftest import make_org_and_user


def make_action(org, user, status, processing_started_at=None, action_type="gift"):
    contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Stuckenbergs")
    db.session.add(contact)
    db.session.flush()

    action = SuggestedAction(
        org_id=org.id, contact_id=contact.id, source_campaign_id=None,
        action_type=action_type, reason_text="Test reason",
        target_date=date.today() + timedelta(days=5),
        status=status, processing_started_at=processing_started_at,
    )
    db.session.add(action)
    db.session.commit()
    return action


def test_stale_processing_row_is_released_to_pending(app, db):
    with app.app_context():
        org, user = make_org_and_user(db)
        stuck_since = datetime.utcnow() - timedelta(minutes=30)
        action = make_action(org, user, "processing", processing_started_at=stuck_since)

        released = reconcile_stuck_processing_actions(stale_after_minutes=10)

        assert len(released) == 1
        assert released[0]["id"] == action.id
        assert released[0]["processing_started_at"] == stuck_since

        refreshed = SuggestedAction.query.get(action.id)
        assert refreshed.status == "pending"
        assert refreshed.processing_started_at is None


def test_recent_processing_row_is_left_alone(app, db):
    """A claim taken 30 seconds ago is a normal in-flight approval
    (e.g. waiting on Stripe), not a stuck one -- must not be touched."""
    with app.app_context():
        org, user = make_org_and_user(db)
        just_now = datetime.utcnow() - timedelta(seconds=30)
        action = make_action(org, user, "processing", processing_started_at=just_now)

        released = reconcile_stuck_processing_actions(stale_after_minutes=10)

        assert released == []
        refreshed = SuggestedAction.query.get(action.id)
        assert refreshed.status == "processing"
        assert refreshed.processing_started_at == just_now


def test_non_processing_rows_are_never_touched(app, db):
    with app.app_context():
        org, user = make_org_and_user(db)
        long_ago = datetime.utcnow() - timedelta(days=1)
        pending = make_action(org, user, "pending")
        approved = make_action(org, user, "approved")
        # Defensive: even if processing_started_at were somehow left set
        # on a non-"processing" row (shouldn't happen given the model's
        # own transitions, but the query itself is the real guarantee).
        stray_timestamp = make_action(org, user, "approved", processing_started_at=long_ago)

        released = reconcile_stuck_processing_actions(stale_after_minutes=10)

        assert released == []
        assert SuggestedAction.query.get(pending.id).status == "pending"
        assert SuggestedAction.query.get(approved.id).status == "approved"
        assert SuggestedAction.query.get(stray_timestamp.id).status == "approved"


def test_multiple_stuck_rows_are_all_released(app, db):
    with app.app_context():
        org, user = make_org_and_user(db)
        old = datetime.utcnow() - timedelta(hours=2)
        a = make_action(org, user, "processing", processing_started_at=old, action_type="gift")
        b = make_action(org, user, "processing", processing_started_at=old, action_type="handwritten_note")

        released = reconcile_stuck_processing_actions(stale_after_minutes=10)

        released_ids = {r["id"] for r in released}
        assert released_ids == {a.id, b.id}
        assert SuggestedAction.query.get(a.id).status == "pending"
        assert SuggestedAction.query.get(b.id).status == "pending"
