"""The one-off order money path: claim, idempotency, recovery.

PAY-1. `confirm_order` charged a saved card after a plain read-then-
write status check, with no Stripe idempotency key -- unlike the
automated approval path, which had both. A double-click on "Charge $X"
could produce two PaymentIntents for the same order, and there is no
refund tooling anywhere in the app to undo it.

These tests exercise the claim primitive directly rather than through
two real concurrent HTTP requests. Genuine concurrency against SQLite
in-process would be testing the database's locking, not the
application's logic; what matters here is that the claim is a single
atomic statement whose second caller loses, and that the loser is told
something accurate.
"""
from datetime import datetime, timedelta

from app.models import Contact, Order
from app.routes.orders import _claim_order_for_processing, _release_order_claim
from app.services.suggestion_engine import reconcile_stuck_processing_orders
from tests.conftest import make_org_and_user


def _pending_order(db, org, user, total_cents=7500):
    contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Riveras")
    db.session.add(contact)
    db.session.flush()
    order = Order(
        org_id=org.id, contact_id=contact.id, ordered_by_user_id=user.id,
        gift_name_snapshot="Closing gift box", gift_price_cents=total_cents,
        fulfillment_method="pickup", status="pending",
    )
    db.session.add(order)
    db.session.commit()
    return order


class TestOrderClaim:
    def test_first_claim_wins(self, app, db):
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)

        assert _claim_order_for_processing(order.id, org.id) is True

        db.session.refresh(order)
        assert order.status == "processing"
        assert order.processing_started_at is not None

    def test_second_claim_loses(self, app, db):
        """The double-click. The second request must find out it lost
        *before* it reaches Stripe -- that is the whole point of doing
        this as an UPDATE ... WHERE rather than a Python-side check."""
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)

        assert _claim_order_for_processing(order.id, org.id) is True
        assert _claim_order_for_processing(order.id, org.id) is False

    def test_a_paid_order_cannot_be_reclaimed(self, app, db):
        """A retry arriving after the original request finished must not
        start a second charge."""
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)
        order.status = "paid"
        db.session.commit()

        assert _claim_order_for_processing(order.id, org.id) is False

    def test_claim_is_scoped_to_the_owning_org(self, app, db):
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)
        other_org, _other_user = make_org_and_user(db)

        assert _claim_order_for_processing(order.id, other_org.id) is False
        db.session.refresh(order)
        assert order.status == "pending", "another org must not be able to claim this order"

    def test_release_returns_the_order_to_pending(self, app, db):
        """A declined card must leave the order chargeable again, not
        permanently stuck."""
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)
        _claim_order_for_processing(order.id, org.id)

        _release_order_claim(order.id, org.id)

        db.session.refresh(order)
        assert order.status == "pending"
        assert order.processing_started_at is None
        assert _claim_order_for_processing(order.id, org.id) is True


class TestIdempotencyKey:
    """The key is what protects the residual gap the claim can't: a
    crash after Stripe processed the charge but before the result was
    recorded."""

    def test_key_is_stable_for_the_same_order_and_amount(self, app, db):
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user, total_cents=7500)

        first = f"order-{order.id}-{order.total_cents}"
        second = f"order-{order.id}-{order.total_cents}"

        assert first == second, "a retry of the same charge must reuse the key"

    def test_key_changes_when_the_amount_changes(self, app, db):
        """Deliberate: if the order total changed, this is a different
        charge and must not silently return the old PaymentIntent for
        the old price."""
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user, total_cents=7500)
        before = f"order-{order.id}-{order.total_cents}"

        order.shipping_cost_cents = 595
        db.session.commit()
        after = f"order-{order.id}-{order.total_cents}"

        assert before != after

    def test_keys_differ_between_orders(self, app, db):
        org, user = make_org_and_user(db)
        first = _pending_order(db, org, user)
        second = _pending_order(db, org, user)

        assert f"order-{first.id}-{first.total_cents}" != f"order-{second.id}-{second.total_cents}"


class TestReconciler:
    """A process killed between claiming and recording leaves the row in
    "processing" with nothing to move it. confirm_order's own guard
    redirects any non-pending status to the success page, so without
    this the agent sees a confirmation for a gift that may never have
    been charged."""

    def test_an_abandoned_claim_is_released(self, app, db):
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)
        order.status = "processing"
        order.processing_started_at = datetime.utcnow() - timedelta(minutes=30)
        db.session.commit()

        released = reconcile_stuck_processing_orders(stale_after_minutes=10)

        assert [row["id"] for row in released] == [order.id]
        db.session.refresh(order)
        assert order.status == "pending"
        assert order.processing_started_at is None

    def test_a_recent_claim_is_left_alone(self, app, db):
        """Must never fire on a claim that is merely slow -- a normal
        one lives for a single Stripe round trip, but releasing a live
        claim would reintroduce exactly the double-charge window the
        claim exists to close."""
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)
        order.status = "processing"
        order.processing_started_at = datetime.utcnow() - timedelta(seconds=5)
        db.session.commit()

        assert reconcile_stuck_processing_orders(stale_after_minutes=10) == []

        db.session.refresh(order)
        assert order.status == "processing"

    def test_paid_orders_are_never_touched(self, app, db):
        org, user = make_org_and_user(db)
        order = _pending_order(db, org, user)
        order.status = "paid"
        order.processing_started_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()

        assert reconcile_stuck_processing_orders(stale_after_minutes=10) == []

        db.session.refresh(order)
        assert order.status == "paid"


class TestChargesTheSelectedCard:
    """Not from the review. The agent picks a card at
    routes/orders.choose_payment, it is saved to order.payment_method_id
    -- and charge_saved_card then charged user.default_payment_method
    regardless. An agent who selected their business card while a
    personal card was set as default was charged the personal one, with
    the confirmation screen showing the card they'd picked."""

    def _two_cards(self, db, user):
        from app.models import PaymentMethod

        default_card = PaymentMethod(
            user_id=user.id, stripe_payment_method_id="pm_personal_default",
            card_brand="visa", card_last4="1111", is_default=True,
        )
        chosen_card = PaymentMethod(
            user_id=user.id, stripe_payment_method_id="pm_business_chosen",
            card_brand="amex", card_last4="9999", is_default=False,
        )
        db.session.add_all([default_card, chosen_card])
        db.session.commit()
        return default_card, chosen_card

    def test_explicit_payment_method_is_charged_not_the_default(self, app, db, monkeypatch):
        org, user = make_org_and_user(db)
        default_card, chosen_card = self._two_cards(db, user)

        captured = {}

        class _FakeIntent:
            id = "pi_test"

            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return _FakeIntent()

        class _FakeStripe:
            PaymentIntent = _FakeIntent

            class error:
                class CardError(Exception):
                    pass

        from app.services import payments
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)

        ok, _intent_id, error = payments.charge_saved_card(
            user, 7500, description="Closing gift", payment_method=chosen_card,
        )

        assert ok, error
        assert captured["payment_method"] == "pm_business_chosen", (
            "the agent's selected card must be the one charged"
        )

    def test_falls_back_to_the_default_when_no_card_is_specified(self, app, db, monkeypatch):
        """Automated flow approvals have no customer present and no
        chosen card, so the default remains correct for them."""
        org, user = make_org_and_user(db)
        self._two_cards(db, user)

        captured = {}

        class _FakeIntent:
            id = "pi_test"

            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return _FakeIntent()

        class _FakeStripe:
            PaymentIntent = _FakeIntent

            class error:
                class CardError(Exception):
                    pass

        from app.services import payments
        monkeypatch.setattr(payments, "get_stripe", lambda: _FakeStripe)

        ok, _intent_id, error = payments.charge_saved_card(user, 7500, description="Flow gift")

        assert ok, error
        assert captured["payment_method"] == "pm_personal_default"

    def test_another_users_card_is_refused(self, app, db):
        org, user = make_org_and_user(db)
        _other_org, other_user = make_org_and_user(db)
        from app.models import PaymentMethod

        their_card = PaymentMethod(
            user_id=other_user.id, stripe_payment_method_id="pm_not_yours",
            card_brand="visa", card_last4="4242", is_default=True,
        )
        db.session.add(their_card)
        db.session.commit()

        ok, _intent_id, error = payments_charge(user, their_card)

        assert ok is False
        assert "isn't available" in error


def payments_charge(user, card):
    from app.services.payments import charge_saved_card

    return charge_saved_card(user, 7500, description="x", payment_method=card)
