"""
Regression tests for Priority 1 of the production-hardening review:
gift/handwritten_note approvals charging a saved card without any
idempotency key or concurrency guard, so a double-click, a retried
request, or two near-simultaneous requests for the same
SuggestedAction could charge the customer more than once.

Covers:
- charge_saved_card() threads a caller-provided idempotency_key
  through to Stripe.
- _claim_action_for_processing() is a true one-winner atomic claim:
  calling it twice for the same pending action, only one call
  succeeds.
- approve_action's gift and handwritten_note paths both use the claim
  + a deterministic per-action, per-type idempotency key.
- A failed charge releases the claim back to "pending" (retryable),
  never leaves an action stuck in "processing".
- Retrying approve_action after it already succeeded is a no-op --
  no second charge, no error.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Contact, GiftCatalogItem, PaymentMethod, SuggestedAction
from app.routes import dashboard as dashboard_module

from tests.conftest import make_org_and_user


def login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = user.id
        sess["_fresh"] = True


def make_gift_setup(db, org, user, price_cents=4900):
    """A billable, shippable Contact + a global gift + a pending gift
    SuggestedAction the agent could approve -- the minimum fixture
    every test in this file needs."""
    contact = Contact(
        org_id=org.id,
        owner_user_id=user.id,
        household_name="The Testers",
        shipping_address_line1="1 Main St",
        shipping_city="Provo",
        shipping_state="UT",
        shipping_zip="84601",
    )
    db.session.add(contact)

    gift = GiftCatalogItem(org_id=None, name="Test Basket", price_cents=price_cents, is_active=True)
    db.session.add(gift)

    payment_method = PaymentMethod(
        user_id=user.id, stripe_payment_method_id="pm_fake_123",
        card_brand="visa", card_last4="4242", is_default=True,
    )
    db.session.add(payment_method)
    db.session.flush()

    action = SuggestedAction(
        org_id=org.id,
        contact_id=contact.id,
        source_campaign_id=None,
        action_type="gift",
        suggested_gift_id=gift.id,
        reason_text="Test reason",
        target_date=date.today() + timedelta(days=3),
        status="pending",
    )
    db.session.add(action)
    db.session.commit()
    return contact, gift, action


def make_handwritten_note_setup(db, org, user):
    contact = Contact(
        org_id=org.id,
        owner_user_id=user.id,
        household_name="The Notes",
        shipping_address_line1="2 Main St",
        shipping_city="Provo",
        shipping_state="UT",
        shipping_zip="84601",
    )
    db.session.add(contact)

    payment_method = PaymentMethod(
        user_id=user.id, stripe_payment_method_id="pm_fake_456",
        card_brand="visa", card_last4="4242", is_default=True,
    )
    db.session.add(payment_method)
    db.session.flush()

    action = SuggestedAction(
        org_id=org.id,
        contact_id=contact.id,
        source_campaign_id=None,
        action_type="handwritten_note",
        reason_text="Test reason",
        generated_message="Congrats!",
        target_date=date.today() + timedelta(days=3),
        status="pending",
    )
    db.session.add(action)
    db.session.commit()
    return contact, action


# --- charge_saved_card: idempotency key actually reaches Stripe ---

def test_charge_saved_card_passes_idempotency_key(app, db, monkeypatch):
    from app.services import payments

    captured = {}

    class FakeIntent:
        id = "pi_fake_1"

    class FakePaymentIntent:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return FakeIntent()

    class FakeStripeModule:
        PaymentIntent = FakePaymentIntent
        error = type("error", (), {"CardError": type("CardError", (Exception,), {})})

    monkeypatch.setattr(payments, "get_stripe", lambda: FakeStripeModule)

    org, user = make_org_and_user(db)
    pm = PaymentMethod(user_id=user.id, stripe_payment_method_id="pm_1", is_default=True)
    db.session.add(pm)
    db.session.commit()

    success, intent_id, error = payments.charge_saved_card(
        user, 1000, "test charge", idempotency_key="suggested-action-abc-gift",
    )

    assert success is True
    assert intent_id == "pi_fake_1"
    assert captured.get("idempotency_key") == "suggested-action-abc-gift"


# --- _claim_action_for_processing: true one-winner semantics ---

def test_claim_is_exclusive(app, db):
    org, user = make_org_and_user(db)
    contact, gift, action = make_gift_setup(db, org, user)

    first = dashboard_module._claim_action_for_processing(action.id, org.id)
    second = dashboard_module._claim_action_for_processing(action.id, org.id)

    assert first is True
    assert second is False, "a second claim attempt on an already-claimed action must not also succeed"
    assert SuggestedAction.query.get(action.id).status == "processing"


def test_claim_fails_once_already_approved(app, db):
    org, user = make_org_and_user(db)
    contact, gift, action = make_gift_setup(db, org, user)
    action.status = "approved"
    db.session.commit()

    assert dashboard_module._claim_action_for_processing(action.id, org.id) is False


# --- approve_action (gift): double approval, retries, failure recovery ---

def test_double_approval_request_charges_only_once(app, db, client, monkeypatch):
    org, user = make_org_and_user(db)
    contact, gift, action = make_gift_setup(db, org, user, price_cents=4900)
    login_as(client, user)

    calls = []

    def fake_charge(billing_agent, amount_cents, description, metadata=None, idempotency_key=None):
        calls.append(idempotency_key)
        return True, "pi_fake_charge", None

    monkeypatch.setattr(dashboard_module, "charge_saved_card", fake_charge)

    resp1 = client.post(f"/dashboard/actions/{action.id}/approve")
    resp2 = client.post(f"/dashboard/actions/{action.id}/approve")

    assert resp1.status_code == 302
    assert resp2.status_code == 302
    assert len(calls) == 1, "a second approve request for the same action must not charge again"
    assert calls[0] == f"suggested-action-{action.id}-gift"

    refreshed = SuggestedAction.query.get(action.id)
    assert refreshed.status == "approved"


def test_retry_after_success_does_not_charge_again(app, db, client, monkeypatch):
    """Simulates a client retrying the exact same approve request after
    the first one already fully succeeded (e.g. it never saw the
    response) -- must be a no-op, not a second charge."""
    org, user = make_org_and_user(db)
    contact, gift, action = make_gift_setup(db, org, user)
    login_as(client, user)

    calls = []

    def fake_charge(billing_agent, amount_cents, description, metadata=None, idempotency_key=None):
        calls.append(idempotency_key)
        return True, "pi_fake_charge", None

    monkeypatch.setattr(dashboard_module, "charge_saved_card", fake_charge)

    client.post(f"/dashboard/actions/{action.id}/approve")
    assert SuggestedAction.query.get(action.id).status == "approved"

    resp = client.post(f"/dashboard/actions/{action.id}/approve")
    assert resp.status_code == 302
    assert len(calls) == 1, "retrying an already-approved action must not trigger another charge"


def test_failed_payment_returns_action_to_pending(app, db, client, monkeypatch):
    org, user = make_org_and_user(db)
    contact, gift, action = make_gift_setup(db, org, user)
    login_as(client, user)

    def fake_charge_declined(billing_agent, amount_cents, description, metadata=None, idempotency_key=None):
        return False, None, "Card was declined."

    monkeypatch.setattr(dashboard_module, "charge_saved_card", fake_charge_declined)

    resp = client.post(f"/dashboard/actions/{action.id}/approve")
    assert resp.status_code == 302

    refreshed = SuggestedAction.query.get(action.id)
    assert refreshed.status == "pending", "a declined charge must release the claim, not leave it stuck processing"


def test_failed_then_retried_payment_can_succeed(app, db, client, monkeypatch):
    """After a decline puts the action back to pending, a real retry
    (a new attempt, not a duplicate of the same in-flight request)
    must be able to go through and actually charge."""
    org, user = make_org_and_user(db)
    contact, gift, action = make_gift_setup(db, org, user)
    login_as(client, user)

    calls = {"count": 0}

    def fake_charge(billing_agent, amount_cents, description, metadata=None, idempotency_key=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return False, None, "Card was declined."
        return True, "pi_fake_charge_2", None

    monkeypatch.setattr(dashboard_module, "charge_saved_card", fake_charge)

    client.post(f"/dashboard/actions/{action.id}/approve")
    assert SuggestedAction.query.get(action.id).status == "pending"

    client.post(f"/dashboard/actions/{action.id}/approve")
    assert calls["count"] == 2
    assert SuggestedAction.query.get(action.id).status == "approved"


# --- handwritten_note path gets the same protection, with its own key ---

def test_handwritten_note_approval_uses_its_own_idempotency_key(app, db, client, monkeypatch):
    org, user = make_org_and_user(db)
    contact, action = make_handwritten_note_setup(db, org, user)
    login_as(client, user)

    calls = []

    def fake_charge(billing_agent, amount_cents, description, metadata=None, idempotency_key=None):
        calls.append(idempotency_key)
        return True, "pi_fake_note", None

    monkeypatch.setattr(dashboard_module, "charge_saved_card", fake_charge)

    resp1 = client.post(f"/dashboard/actions/{action.id}/approve")
    resp2 = client.post(f"/dashboard/actions/{action.id}/approve")

    assert resp1.status_code == 302
    assert resp2.status_code == 302
    assert len(calls) == 1
    assert calls[0] == f"suggested-action-{action.id}-handwritten-note"
    assert SuggestedAction.query.get(action.id).status == "approved"
