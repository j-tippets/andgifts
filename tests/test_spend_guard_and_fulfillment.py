"""Spend ceilings, and where fulfillment notices actually go.

Items 13 (cheap form) and 14. Both are about the gap between "the card
was charged" and "the right thing happened afterwards".
"""
import uuid
from datetime import datetime, timedelta

from app.models import ActionLog, Contact, PaymentMethod
from app.services import payments
from app.services.email import _wdf_recipient
from tests.conftest import make_org_and_user


class _FakeIntent:
    id = "pi_test"

    @staticmethod
    def create(**kwargs):
        return _FakeIntent()


class _FakeStripe:
    PaymentIntent = _FakeIntent

    class error:
        class CardError(Exception):
            pass


def _user_with_card(db):
    org, user = make_org_and_user(db)
    db.session.add(PaymentMethod(
        # Unique per call -- stripe_payment_method_id is UNIQUE, and
        # the cross-agent test builds two.
        user_id=user.id, stripe_payment_method_id=f"pm_{uuid.uuid4().hex[:10]}",
        card_brand="visa", card_last4="4242", is_default=True,
    ))
    db.session.commit()
    return org, user


def _log_spend(db, org, user, cost_cents, hours_ago=1):
    contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Bergs")
    db.session.add(contact)
    db.session.flush()
    db.session.add(ActionLog(
        org_id=org.id, contact_id=contact.id, action_type="gift",
        cost_cents=cost_cents, approved_by_user_id=user.id,
        sent_at=datetime.utcnow() - timedelta(hours=hours_ago),
    ))
    db.session.commit()


class TestSingleChargeCeiling:
    def test_a_normal_charge_goes_through(self, app, db, monkeypatch):
        _org, user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)

        ok, _id, error = payments.charge_saved_card(user, 7500, description="Closing gift")

        assert ok, error

    def test_an_absurd_charge_is_refused(self, app, db, monkeypatch):
        """A bad price field or a unit mix-up (dollars entered as cents)
        should not reach Stripe at all."""
        _org, user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)

        ok, intent_id, error = payments.charge_saved_card(
            user, 5_000_00, description="Oops",
        )

        assert ok is False
        assert intent_id is None
        assert "single-charge limit" in error

    def test_the_ceiling_is_checked_before_stripe(self, app, db):
        """No Stripe configured at all: the guard must still refuse, and
        with the spend reason rather than a config message -- proving no
        PaymentIntent could have been created."""
        _org, user = _user_with_card(db)

        ok, _id, error = payments.charge_saved_card(user, 5_000_00, description="Oops")

        assert ok is False
        assert "single-charge limit" in error

    def test_the_limit_is_configurable(self, app, db, monkeypatch):
        _org, user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)
        app.config["MAX_SINGLE_CHARGE_CENTS"] = 5000

        ok, _id, error = payments.charge_saved_card(user, 7500, description="Closing gift")

        assert ok is False
        assert "single-charge limit" in error


class TestDailyCeiling:
    """A runaway flow charges many normal-sized amounts rather than one
    huge one, so the per-charge ceiling alone would never catch it."""

    def test_charges_under_the_daily_total_go_through(self, app, db, monkeypatch):
        org, user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)
        _log_spend(db, org, user, 50_000)

        ok, _id, error = payments.charge_saved_card(user, 7500, description="Closing gift")

        assert ok, error

    def test_a_charge_crossing_the_daily_total_is_refused(self, app, db, monkeypatch):
        org, user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)
        app.config["MAX_AGENT_DAILY_CHARGE_CENTS"] = 100_00
        _log_spend(db, org, user, 95_00)

        ok, _id, error = payments.charge_saved_card(user, 20_00, description="Closing gift")

        assert ok is False
        assert "daily spend limit" in error

    def test_spend_older_than_24_hours_does_not_count(self, app, db, monkeypatch):
        """Rolling window, not a running total -- yesterday's legitimate
        activity must not block today's."""
        org, user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)
        app.config["MAX_AGENT_DAILY_CHARGE_CENTS"] = 100_00
        _log_spend(db, org, user, 95_00, hours_ago=30)

        ok, _id, error = payments.charge_saved_card(user, 20_00, description="Closing gift")

        assert ok, error

    def test_another_agents_spend_does_not_count(self, app, db, monkeypatch):
        """The cap is per agent. One busy agent must not block their
        colleague."""
        org, user = _user_with_card(db)
        _other_org, other_user = _user_with_card(db)
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)
        app.config["MAX_AGENT_DAILY_CHARGE_CENTS"] = 100_00
        _log_spend(db, org, other_user, 95_00)

        ok, _id, error = payments.charge_saved_card(user, 20_00, description="Closing gift")

        assert ok, error


class TestWdfRecipient:
    """Fulfillment for every customer depends on someone reading these,
    and the card is already charged by the time one is sent."""

    def test_the_configured_address_is_used(self, app):
        app.config["WDF_FULFILLMENT_EMAIL"] = "fulfillment@example.com"

        assert _wdf_recipient() == "fulfillment@example.com"

    def test_it_falls_back_to_the_support_inbox(self, app):
        """An unset address should degrade to a monitored mailbox, not
        to a private one."""
        app.config["WDF_FULFILLMENT_EMAIL"] = ""
        app.config["SUPPORT_INBOX_EMAIL"] = "support@example.com"

        assert _wdf_recipient() == "support@example.com"

    def test_no_address_configured_returns_none(self, app):
        """So the caller can raise the alarm rather than silently
        dropping a paid order."""
        app.config["WDF_FULFILLMENT_EMAIL"] = ""
        app.config["SUPPORT_INBOX_EMAIL"] = ""

        assert _wdf_recipient() is None

    def test_no_personal_address_remains_in_the_source(self):
        """The specific thing item 14 was about."""
        import pathlib

        source = pathlib.Path(payments.__file__).parent / "email.py"
        assert "jtippets@outlook.com" not in source.read_text()
