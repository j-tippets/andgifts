"""
Regression tests for the Team onboarding billing bypass (security
review Phase 1, item 1). The bug: selecting "Team" in the signup
wizard granted org.tier = "team" immediately, before any Stripe
interaction -- so abandoning the wizard before paying, or a
missing/misconfigured Stripe setup, left an org fully entitled to
Team forever with no subscription. These tests assert the fix: tier
is never granted until confirm_team_subscription_from_checkout_session
(or its webhook counterpart) independently validates a completed
Stripe Checkout Session for the SAME org.
"""
from app.extensions import db
from app.models import Org
from app.services import org_billing

from tests.conftest import make_org_and_user


class FakeStripeObject(dict):
    """Minimal stand-in for Stripe SDK objects, which support both
    dict-style .get() and attribute access."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def make_fake_session(org_id, price_id="price_team_123", customer="cus_abc",
                       status="complete", mode="subscription",
                       sub_org_id=None, subscription_id="sub_123"):
    sub_org_id = org_id if sub_org_id is None else sub_org_id
    subscription = FakeStripeObject({
        "id": subscription_id,
        "metadata": {"org_id": sub_org_id},
        "items": {"data": [{"price": {"id": price_id}}]},
        "default_payment_method": None,
    })
    return FakeStripeObject({
        "mode": mode,
        "status": status,
        "client_reference_id": org_id,
        "metadata": {"org_id": org_id},
        "customer": customer,
        "subscription": subscription,
    })


class FakeStripeClient:
    """Stands in for the `stripe` module: only checkout.Session.retrieve
    is exercised by confirm_team_subscription_from_checkout_session."""
    def __init__(self, session):
        self._session = session

        class _Checkout:
            def __init__(inner_self):
                inner_self.Session = self

        self.checkout = _Checkout()

    def retrieve(self, session_id, expand=None):
        return self._session


# --- Wizard-level: tier must never be granted before Stripe confirms ---

def test_selecting_team_does_not_grant_tier_immediately(client, db, wizard_session):
    org, user = make_org_and_user(db, tier="free", onboarding_step="plan")
    wizard_session(org, user)

    resp = client.post("/get-started/plan", data={"tier": "team"})
    assert resp.status_code == 302

    refreshed = Org.query.get(org.id)
    assert refreshed.tier == "free", "org.tier must stay free until Stripe confirms Team"
    assert refreshed.onboarding_step == "invites"


def test_abandoned_wizard_never_grants_team(client, db, wizard_session):
    """Picks Team, skips invites, but never reaches billing_start or
    billing_return -- simulates closing the tab mid-wizard."""
    org, user = make_org_and_user(db, tier="free", onboarding_step="plan")
    wizard_session(org, user)

    client.post("/get-started/plan", data={"tier": "team"})
    client.post("/get-started/team/skip")

    refreshed = Org.query.get(org.id)
    assert refreshed.tier == "free"
    assert refreshed.onboarding_step == "billing"


def test_billing_return_without_session_id_does_not_grant_team(client, db, wizard_session):
    """Someone hitting the return URL directly (no session_id) --
    e.g. a bookmarked/guessed link -- must not finish the wizard as
    Team."""
    org, user = make_org_and_user(db, tier="free", onboarding_step="billing")
    wizard_session(org, user)

    resp = client.get("/get-started/billing/return")
    assert resp.status_code == 302

    refreshed = Org.query.get(org.id)
    assert refreshed.tier == "free"
    assert refreshed.onboarding_step == "done"  # wizard finishes, just not as Team


def test_billing_start_missing_stripe_config_fails_closed_in_production(app, client, db, wizard_session):
    app.config["ENV_NAME"] = "production"
    org, user = make_org_and_user(db, tier="free", onboarding_step="billing")
    wizard_session(org, user)

    resp = client.post("/get-started/billing/start")
    assert resp.status_code == 302
    assert "/get-started/billing" in resp.headers["Location"]

    refreshed = Org.query.get(org.id)
    assert refreshed.tier == "free"
    assert refreshed.onboarding_step == "billing", "must NOT finish signup when Stripe is unavailable in prod"


def test_billing_start_missing_stripe_config_dev_fallback_finishes_as_free(app, client, db, wizard_session):
    app.config["ENV_NAME"] = "development"
    org, user = make_org_and_user(db, tier="free", onboarding_step="billing")
    wizard_session(org, user)

    client.post("/get-started/billing/start")

    refreshed = Org.query.get(org.id)
    assert refreshed.tier == "free", "dev fallback must finish on Free, never silently grant Team"
    assert refreshed.onboarding_step == "done"


def test_team_route_unreachable_by_setting_onboarding_step_alone(client, db, wizard_session):
    """org.onboarding_step is user-un-reachable directly, but this
    guards against ever reintroducing an org.tier-based gate: an org
    sitting at tier=free/step=plan can't jump straight to the billing
    page."""
    org, user = make_org_and_user(db, tier="free", onboarding_step="plan")
    wizard_session(org, user)

    resp = client.get("/get-started/billing")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/get-started/plan")


# --- confirm_team_subscription_from_checkout_session: validation ---

def test_confirm_rejects_org_id_mismatch(app, db, monkeypatch):
    org, _ = make_org_and_user(db, tier="free", onboarding_step="billing")
    other_org_id = "some-other-org-id"
    fake_session = make_fake_session(org_id=other_org_id)
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeClient(fake_session))

    confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_1")

    assert confirmed is False
    assert Org.query.get(org.id).tier == "free"


def test_confirm_rejects_subscription_org_id_mismatch(app, db, monkeypatch):
    org, _ = make_org_and_user(db, tier="free", onboarding_step="billing")
    fake_session = make_fake_session(org_id=org.id, sub_org_id="different-org")
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeClient(fake_session))

    confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_2")

    assert confirmed is False
    assert Org.query.get(org.id).tier == "free"


def test_confirm_rejects_wrong_price(app, db, monkeypatch):
    org, _ = make_org_and_user(db, tier="free", onboarding_step="billing")
    fake_session = make_fake_session(org_id=org.id, price_id="price_totally_different")
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeClient(fake_session))
    app.config["STRIPE_PRICE_IDS"] = {"team": "price_team_123"}

    confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_3")

    assert confirmed is False
    assert Org.query.get(org.id).tier == "free"


def test_confirm_rejects_incomplete_session(app, db, monkeypatch):
    org, _ = make_org_and_user(db, tier="free", onboarding_step="billing")
    fake_session = make_fake_session(org_id=org.id, status="open")
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeClient(fake_session))

    confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_4")

    assert confirmed is False
    assert Org.query.get(org.id).tier == "free"


def test_confirm_rejects_customer_mismatch(app, db, monkeypatch):
    org, _ = make_org_and_user(db, tier="free", onboarding_step="billing")
    org.stripe_customer_id = "cus_expected"
    db.session.commit()
    fake_session = make_fake_session(org_id=org.id, customer="cus_unexpected")
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeClient(fake_session))

    confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_5")

    assert confirmed is False
    assert Org.query.get(org.id).tier == "free"


def test_confirm_grants_team_on_valid_session_and_is_idempotent(app, db, monkeypatch):
    org, _ = make_org_and_user(db, tier="free", onboarding_step="billing")
    fake_session = make_fake_session(org_id=org.id, price_id="price_team_123")
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeClient(fake_session))
    app.config["STRIPE_PRICE_IDS"] = {"team": "price_team_123"}

    confirmed, shared = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_6")
    assert confirmed is True
    assert Org.query.get(org.id).tier == "team"

    # Re-hitting confirm with the same session (webhook arrives
    # after browser-return already confirmed, or vice versa) must
    # not error or re-fire the upgrade event a second time.
    events_before = db.session.query(db.func.count()).select_from(
        __import__("app.models", fromlist=["OrgEventLog"]).OrgEventLog
    ).scalar()
    confirmed_again, _ = org_billing.confirm_team_subscription_from_checkout_session(org, "cs_test_6")
    events_after = db.session.query(db.func.count()).select_from(
        __import__("app.models", fromlist=["OrgEventLog"]).OrgEventLog
    ).scalar()

    assert confirmed_again is True
    assert Org.query.get(org.id).tier == "team"
    assert events_after == events_before, "idempotent re-confirmation must not double-log an upgrade event"
