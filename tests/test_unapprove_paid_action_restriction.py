"""
Regression tests for Priority 2 of the production-hardening review:
unapprove_action let ANY approved action -- including a gift or
handwritten_note that had already charged a real Stripe card -- go
back to "pending" while deleting the ActionLog row that was the only
record of that charge. Re-approving afterward would charge a second
time, and the deleted ActionLog permanently destroyed financial
history.

Covers:
- Email approval can still be undone (existing behavior preserved).
- Gift approval cannot be reset to pending via unapprove_action.
- Handwritten-note approval cannot be reset to pending via
  unapprove_action.
- The ActionLog row is never deleted for either paid type.
"""
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import ActionLog, Contact, SuggestedAction

from tests.conftest import make_org_and_user
from tests.test_action_approval_idempotency import login_as


def make_approved_action(db, org, user, action_type, contact_name="The Approved"):
    contact = Contact(org_id=org.id, owner_user_id=user.id, household_name=contact_name)
    db.session.add(contact)
    db.session.flush()

    action = SuggestedAction(
        org_id=org.id,
        contact_id=contact.id,
        source_campaign_id=None,
        action_type=action_type,
        reason_text="Test reason",
        generated_message="Test message" if action_type != "gift" else None,
        target_date=date.today() + timedelta(days=1),
        status="approved",
        resolved_at=datetime.utcnow(),
    )
    db.session.add(action)
    db.session.flush()

    log = ActionLog(
        org_id=org.id,
        contact_id=contact.id,
        suggested_action_id=action.id,
        action_type=action_type,
        detail="Test detail",
        cost_cents=4900 if action_type != "email" else None,
    )
    db.session.add(log)
    db.session.commit()
    return contact, action, log


def test_email_approval_can_still_be_undone(app, db, client):
    org, user = make_org_and_user(db)
    contact, action, log = make_approved_action(db, org, user, "email")
    login_as(client, user)

    resp = client.post(f"/dashboard/actions/{action.id}/unapprove")
    assert resp.status_code == 302

    refreshed = SuggestedAction.query.get(action.id)
    assert refreshed.status == "pending"
    assert ActionLog.query.filter_by(suggested_action_id=action.id).count() == 0


def test_gift_approval_cannot_be_unapproved(app, db, client):
    org, user = make_org_and_user(db)
    contact, action, log = make_approved_action(db, org, user, "gift")
    login_as(client, user)

    resp = client.post(f"/dashboard/actions/{action.id}/unapprove")
    assert resp.status_code == 302

    refreshed = SuggestedAction.query.get(action.id)
    assert refreshed.status == "approved", "a paid gift approval must not be reset to pending"


def test_handwritten_note_approval_cannot_be_unapproved(app, db, client):
    org, user = make_org_and_user(db)
    contact, action, log = make_approved_action(db, org, user, "handwritten_note")
    login_as(client, user)

    resp = client.post(f"/dashboard/actions/{action.id}/unapprove")
    assert resp.status_code == 302

    refreshed = SuggestedAction.query.get(action.id)
    assert refreshed.status == "approved", "a paid handwritten_note approval must not be reset to pending"


def test_paid_action_log_is_never_deleted(app, db, client):
    org, user = make_org_and_user(db)

    _, gift_action, gift_log = make_approved_action(db, org, user, "gift", contact_name="Gift Contact")
    _, note_action, note_log = make_approved_action(db, org, user, "handwritten_note", contact_name="Note Contact")
    login_as(client, user)

    client.post(f"/dashboard/actions/{gift_action.id}/unapprove")
    client.post(f"/dashboard/actions/{note_action.id}/unapprove")

    assert ActionLog.query.get(gift_log.id) is not None, "financial ActionLog for a gift must survive an unapprove attempt"
    assert ActionLog.query.get(note_log.id) is not None, "financial ActionLog for a handwritten_note must survive an unapprove attempt"
