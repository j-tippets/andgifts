"""Approval gating: unresolved gifts, and clobbering resolved actions.

Two failures that both end with an agent believing something happened
that didn't.
"""
from datetime import date, datetime, timedelta

from app.models import Contact, GiftCatalogItem, SuggestedAction
from app.routes.dashboard import _resolve_pending_action
from tests.conftest import make_org_and_user


def _gift_action(db, org, user, suggested_gift=None, status="pending"):
    contact = Contact(
        org_id=org.id, owner_user_id=user.id, household_name="The Nakamuras",
        shipping_address_line1="1 Main St", shipping_city="American Fork",
        shipping_state="UT", shipping_zip="84003",
    )
    db.session.add(contact)
    db.session.flush()
    action = SuggestedAction(
        org_id=org.id, contact_id=contact.id, action_type="gift",
        target_date=date.today() + timedelta(days=7), status=status,
        reason_text="Closing anniversary is coming up.",
        suggested_gift_id=suggested_gift.id if suggested_gift else None,
    )
    db.session.add(action)
    db.session.commit()
    return action


def _catalog_item(db, org, name="Closing gift box", price_cents=7500):
    item = GiftCatalogItem(
        org_id=org.id, name=name, price_cents=price_cents, stock_quantity=10,
    )
    db.session.add(item)
    db.session.commit()
    return item


class TestGiftReadiness:
    """The silent product failure. approve_action's whole
    charge/order/notify block is gated on `action.suggested_gift` being
    present. With no catalog item resolved, execution falls through to
    the plain `else` branch and the action is marked "approved" having
    charged nothing, created no Order, and told WDF nothing. The agent
    sees a success and believes a gift is on its way."""

    def test_a_gift_with_no_catalog_item_is_blocked(self, app, db):
        org, user = make_org_and_user(db)
        action = _gift_action(db, org, user, suggested_gift=None)

        assert action.readiness_blocked_reason is not None
        assert "gift" in action.readiness_blocked_reason.lower()

    def test_a_gift_with_a_catalog_item_is_not_blocked(self, app, db):
        org, user = make_org_and_user(db)
        item = _catalog_item(db, org)
        action = _gift_action(db, org, user, suggested_gift=item)

        assert action.readiness_blocked_reason is None

    def test_the_block_is_flagged_as_agent_resolvable(self, app, db):
        """needs_gift_selection is what keeps the picker and approve
        form on the card. Without it, adding this blocking reason would
        hide the exact control that resolves it and strand the
        suggestion permanently -- a worse bug than the one being
        fixed."""
        org, user = make_org_and_user(db)
        action = _gift_action(db, org, user, suggested_gift=None)

        assert action.needs_gift_selection is True

    def test_other_blocking_reasons_are_not_agent_resolvable(self, app, db):
        """A missing shipping address can't be fixed from the card, so
        the picker should stay hidden for it."""
        org, user = make_org_and_user(db)
        item = _catalog_item(db, org)
        action = _gift_action(db, org, user, suggested_gift=item)
        action.contact.shipping_address_line1 = None
        db.session.commit()

        assert action.readiness_blocked_reason is not None
        assert action.needs_gift_selection is False

    def test_out_of_stock_still_blocks(self, app, db):
        """Regression guard: the new missing-gift branch sits directly
        above the stock check and must not shadow it."""
        org, user = make_org_and_user(db)
        item = _catalog_item(db, org)
        item.stock_quantity = 0
        db.session.commit()
        action = _gift_action(db, org, user, suggested_gift=item)

        assert action.readiness_blocked_reason is not None
        assert "stock" in action.readiness_blocked_reason.lower()


class TestSkipDeleteGuards:
    """skip_action and delete_action assigned status unconditionally, so
    a POST could overwrite any state -- including an approved gift whose
    card was charged and whose order is already with WDF."""

    def test_a_pending_action_can_be_skipped(self, app, db):
        org, user = make_org_and_user(db)
        action = _gift_action(db, org, user, _catalog_item(db, org))

        assert _resolve_pending_action(action.id, org.id, "skipped") is True
        db.session.commit()

        db.session.refresh(action)
        assert action.status == "skipped"
        assert action.resolved_at is not None

    def test_an_approved_action_cannot_be_skipped(self, app, db):
        """The money case. Overwriting "approved" with "skipped" would
        discard the only in-app record that a charge happened."""
        org, user = make_org_and_user(db)
        action = _gift_action(db, org, user, _catalog_item(db, org), status="approved")

        assert _resolve_pending_action(action.id, org.id, "skipped") is False

        db.session.rollback()
        db.session.refresh(action)
        assert action.status == "approved"

    def test_an_action_mid_charge_cannot_be_deleted(self, app, db):
        """"processing" is a claim held by an in-flight charge. Letting
        a delete through here means the charge completes and writes
        "approved" over the top, silently discarding the agent's
        delete -- or, on the other interleaving, discarding the
        approval."""
        org, user = make_org_and_user(db)
        action = _gift_action(db, org, user, _catalog_item(db, org), status="processing")

        assert _resolve_pending_action(action.id, org.id, "deleted") is False

        db.session.rollback()
        db.session.refresh(action)
        assert action.status == "processing"

    def test_a_second_skip_loses(self, app, db):
        """Double-tap on the swipe gesture."""
        org, user = make_org_and_user(db)
        action = _gift_action(db, org, user, _catalog_item(db, org))

        assert _resolve_pending_action(action.id, org.id, "skipped") is True
        db.session.commit()
        assert _resolve_pending_action(action.id, org.id, "skipped") is False

    def test_another_org_cannot_resolve_this_action(self, app, db):
        org, user = make_org_and_user(db)
        other_org, _other_user = make_org_and_user(db)
        action = _gift_action(db, org, user, _catalog_item(db, org))

        assert _resolve_pending_action(action.id, other_org.id, "deleted") is False

        db.session.rollback()
        db.session.refresh(action)
        assert action.status == "pending"
