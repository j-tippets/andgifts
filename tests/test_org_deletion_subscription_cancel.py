"""
Regression tests for Priority 3 of the production-hardening review:
delete_org_completely deleted an org (and every row scoped to it)
without ever canceling its Stripe subscription first -- a deleted
org's billing contact would keep being charged for a subscription
nobody could see or cancel anymore.

Covers cancel_org_subscription directly (services/org_billing.py):
- No subscription on file -> proceeds, nothing to cancel.
- Active subscription -> canceled successfully.
- Cancellation itself fails (Stripe error) -> fails closed, caller
  must not delete.
- Already-canceled subscription -> treated as success (idempotent
  retry), and doesn't attempt a redundant cancel call.
- Retry after a transient failure can still succeed.

And the route (profile.delete_account) end to end:
- A failed cancellation leaves the org, its data, and the user's
  session completely untouched -- nothing partially deleted.
"""
from app.extensions import db
from app.models import Org
from app.services import org_billing

from tests.conftest import make_org_and_user
from tests.test_action_approval_idempotency import login_as


class FakeSubscription(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class FakeStripeSubscriptionAPI:
    def __init__(self, retrieve_result=None, retrieve_error=None, cancel_error=None):
        self._retrieve_result = retrieve_result
        self._retrieve_error = retrieve_error
        self._cancel_error = cancel_error
        self.cancel_calls = []

    def retrieve(self, subscription_id):
        if self._retrieve_error:
            raise self._retrieve_error
        return self._retrieve_result

    def cancel(self, subscription_id):
        self.cancel_calls.append(subscription_id)
        if self._cancel_error:
            raise self._cancel_error
        return FakeSubscription({"id": subscription_id, "status": "canceled"})


class FakeStripeModule:
    def __init__(self, subscription_api):
        self.Subscription = subscription_api


def test_no_subscription_proceeds(app, db, monkeypatch):
    org, user = make_org_and_user(db, stripe_subscription_id=None)
    # get_stripe deliberately not patched at all -- config.STRIPE_SECRET_KEY
    # is empty in the testing config, so get_stripe() already returns None
    # here, exercising the real "Stripe not configured" branch too.
    ok, error = org_billing.cancel_org_subscription(org)
    assert ok is True
    assert error is None


def test_active_subscription_canceled_successfully(app, db, monkeypatch):
    org, user = make_org_and_user(db, stripe_subscription_id="sub_active_123")
    api = FakeStripeSubscriptionAPI(retrieve_result=FakeSubscription({"status": "active"}))
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeModule(api))

    ok, error = org_billing.cancel_org_subscription(org)

    assert ok is True
    assert error is None
    assert api.cancel_calls == ["sub_active_123"]


def test_already_canceled_subscription_is_treated_as_success(app, db, monkeypatch):
    org, user = make_org_and_user(db, stripe_subscription_id="sub_already_gone")
    api = FakeStripeSubscriptionAPI(retrieve_result=FakeSubscription({"status": "canceled"}))
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeModule(api))

    ok, error = org_billing.cancel_org_subscription(org)

    assert ok is True
    assert error is None
    assert api.cancel_calls == [], "an already-canceled subscription shouldn't get a redundant cancel call"


def test_cancellation_failure_is_reported(app, db, monkeypatch):
    org, user = make_org_and_user(db, stripe_subscription_id="sub_will_fail")
    api = FakeStripeSubscriptionAPI(
        retrieve_result=FakeSubscription({"status": "active"}),
        cancel_error=Exception("Stripe API is down"),
    )
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeModule(api))

    ok, error = org_billing.cancel_org_subscription(org)

    assert ok is False
    assert "Stripe API is down" in error


def test_retry_after_transient_error_can_succeed(app, db, monkeypatch):
    org, user = make_org_and_user(db, stripe_subscription_id="sub_retry_me")

    failing_api = FakeStripeSubscriptionAPI(
        retrieve_result=FakeSubscription({"status": "active"}),
        cancel_error=Exception("Network timeout"),
    )
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeModule(failing_api))
    first_ok, first_error = org_billing.cancel_org_subscription(org)
    assert first_ok is False

    working_api = FakeStripeSubscriptionAPI(retrieve_result=FakeSubscription({"status": "active"}))
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeModule(working_api))
    second_ok, second_error = org_billing.cancel_org_subscription(org)

    assert second_ok is True
    assert second_error is None
    assert working_api.cancel_calls == ["sub_retry_me"]


# --- End to end through the route: a failed cancellation must leave everything intact ---

def test_failed_cancellation_prevents_account_deletion(app, db, client, monkeypatch):
    org, user = make_org_and_user(db, stripe_subscription_id="sub_blocks_delete")
    org_id = org.id

    api = FakeStripeSubscriptionAPI(
        retrieve_result=FakeSubscription({"status": "active"}),
        cancel_error=Exception("Stripe is unreachable"),
    )
    monkeypatch.setattr(org_billing, "get_stripe", lambda: FakeStripeModule(api))

    login_as(client, user)
    resp = client.post(
        "/profile/delete",
        data={"current_password": "correct horse battery staple"},
    )

    assert resp.status_code == 302
    assert Org.query.get(org_id) is not None, "org must survive a failed subscription cancellation"

    with client.session_transaction() as sess:
        assert sess.get("_user_id") == user.id, "the user must still be logged in after a blocked deletion"
