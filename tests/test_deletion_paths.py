"""Deletion paths, against realistically-populated records.

The bug class these cover: every delete route in the app clears *some*
of the references pointing at the row being removed. Under SQLite with
FK enforcement off -- how this suite ran until recently -- missing one
is invisible. Under production MySQL it is a 500 on a button the
customer just pressed.

So each test here deliberately builds a record with the associated data
a real one would have, rather than a bare row. A test that deletes an
untouched contact proves nothing; it is precisely the contact with an
order, an interest tag and an audit trail that used to fail.
"""
from sqlalchemy import text

from app.models import (
    ActionLog,
    Campaign,
    CampaignRecipe,
    Contact,
    FillerActionState,
    FlowRecommendation,
    Interest,
    MilestonePriority,
    Order,
    PaymentMethod,
    User,
)
from app.extensions import db as _db
from app.services.user_deletion import detach_and_delete_user
from tests.conftest import make_org_and_user


def _add_member(db, org, email="member@example.com", role="agent"):
    user = User(
        org_id=org.id, email=email, first_name="Team", last_name="Member",
        role=role, email_verified=True,
    )
    user.set_password("correct horse battery staple")
    db.session.add(user)
    db.session.flush()
    return user


def _order_for(db, org, contact, user, **kwargs):
    order = Order(
        org_id=org.id, contact_id=contact.id, ordered_by_user_id=user.id,
        gift_name_snapshot="Closing gift box", gift_price_cents=7500,
        fulfillment_method="shipping", status="paid", **kwargs
    )
    db.session.add(order)
    db.session.flush()
    return order


class TestMemberDeletion:
    """DB-1. A departing agent who had done anything at all could not be
    removed: eleven FKs point at users.id and delete_member cleared
    three."""

    def test_member_with_a_saved_card_and_history_can_be_deleted(self, app, db):
        org, admin = make_org_and_user(db)
        member = _add_member(db, org)

        contact = Contact(org_id=org.id, owner_user_id=member.id, household_name="The Smiths")
        db.session.add(contact)
        db.session.flush()

        db.session.add(PaymentMethod(
            user_id=member.id, stripe_payment_method_id="pm_member_card",
            card_brand="visa", card_last4="4242", is_default=True,
        ))
        db.session.add(FlowRecommendation(
            org_id=org.id, user_id=member.id, event_type="birthday",
            event_label="Birthday", contact_count=1,
        ))
        db.session.add(FillerActionState(
            org_id=org.id, user_id=member.id, filler_key="add_contact", status="dismissed",
        ))
        db.session.add(MilestonePriority(user_id=member.id, event_type="birthday", priority=60))
        _order_for(db, org, contact, member)
        db.session.commit()

        member_id = member.id
        detach_and_delete_user(member)
        db.session.commit()

        assert db.session.get(User, member_id) is None

    def test_deleting_a_member_destroys_their_saved_card(self, app, db):
        """The security-relevant half. A departed agent's card must stop
        being chargeable -- automated flow approvals charge saved cards
        with no per-charge confirmation."""
        org, _admin = make_org_and_user(db)
        member = _add_member(db, org)
        db.session.add(PaymentMethod(
            user_id=member.id, stripe_payment_method_id="pm_should_not_survive",
            card_brand="visa", card_last4="1111", is_default=True,
        ))
        db.session.commit()

        detach_and_delete_user(member)
        db.session.commit()

        assert PaymentMethod.query.filter_by(
            stripe_payment_method_id="pm_should_not_survive"
        ).count() == 0

    def test_deleting_a_member_preserves_financial_history(self, app, db):
        """The other half. Spend and tax records are org-level and must
        outlive whoever happened to place them -- the user link is
        cleared, the row is not."""
        org, admin = make_org_and_user(db)
        member = _add_member(db, org)
        contact = Contact(org_id=org.id, owner_user_id=admin.id, household_name="The Joneses")
        db.session.add(contact)
        db.session.flush()
        order = _order_for(db, org, contact, member)
        order_id = order.id
        db.session.commit()

        detach_and_delete_user(member)
        db.session.commit()

        surviving = db.session.get(Order, order_id)
        assert surviving is not None, "an order must not vanish with the user who placed it"
        assert surviving.ordered_by_user_id is None
        assert surviving.gift_price_cents == 7500


class TestContactDeletion:
    """DB-3. orders.contact_id was a non-nullable FK with no cascade, so
    deleting any contact who had ever been sent a gift 500'd."""

    def test_contact_with_an_order_can_be_deleted(self, app, db):
        org, user = make_org_and_user(db)
        contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Patels")
        db.session.add(contact)
        db.session.flush()
        _order_for(db, org, contact, user)
        db.session.commit()

        contact_id = contact.id
        # Mirrors the route's sequence (routes/contacts.delete_contact)
        # for the order and interest handling specifically.
        Order.query.filter_by(contact_id=contact_id).update(
            {"contact_name_snapshot": "The Patels", "contact_id": None},
            synchronize_session=False,
        )
        db.session.delete(contact)
        db.session.commit()

        assert db.session.get(Contact, contact_id) is None

    def test_the_order_survives_and_remembers_who_it_was_for(self, app, db):
        """The business rule the migration encodes: an agent removing a
        client from the CRM must not erase the record of money they
        actually spent, and that record has to stay legible."""
        org, user = make_org_and_user(db)
        contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Okonkwos")
        db.session.add(contact)
        db.session.flush()
        order = _order_for(db, org, contact, user)
        order_id = order.id
        db.session.commit()

        Order.query.filter_by(contact_id=contact.id).update(
            {"contact_name_snapshot": "The Okonkwos", "contact_id": None},
            synchronize_session=False,
        )
        db.session.delete(contact)
        db.session.commit()

        surviving = db.session.get(Order, order_id)
        assert surviving is not None
        assert surviving.contact_id is None
        assert surviving.contact_name_snapshot == "The Okonkwos"

    def test_contact_with_an_interest_tag_can_be_deleted(self, app, db):
        """contact_interests is a plain join table with no cascade, and
        was missed by both delete paths."""
        org, user = make_org_and_user(db)
        contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The Larssons")
        interest = Interest(name="Gardening")
        db.session.add_all([contact, interest])
        db.session.flush()
        contact.interests.append(interest)
        db.session.commit()

        contact_id = contact.id
        contact.interests.clear()
        db.session.delete(contact)
        db.session.commit()

        assert db.session.get(Contact, contact_id) is None


class TestRecipeDeletion:
    """DB-4, which turned out to be a schema-drift artifact rather than
    a production bug: migration a1c8e2f4b9d0 had already set
    ondelete='SET NULL' on campaigns.source_recipe_id, but the model
    never reflected it, so only the test schema enforced RESTRICT.

    This test now passes because the model was corrected. It is kept as
    the behavioural counterpart to test_schema_matches_migrations --
    that one catches the drift, this one states what the behaviour is
    supposed to be."""

    def test_deleting_a_recipe_leaves_forked_campaigns_intact(self, app, db):
        org, user = make_org_and_user(db)
        recipe = CampaignRecipe(
            org_id=org.id, name="Closing Day Flow",
            event_type="closing_anniversary", action_type="gift",
        )
        db.session.add(recipe)
        db.session.flush()

        campaign = Campaign(
            org_id=org.id, owner_user_id=user.id, created_by_user_id=user.id,
            name="Closing Day Flow", source_recipe_id=recipe.id,
            event_type="closing_anniversary", action_type="gift",
        )
        db.session.add(campaign)
        db.session.commit()
        campaign_id = campaign.id

        db.session.delete(recipe)
        db.session.commit()

        surviving = db.session.get(Campaign, campaign_id)
        assert surviving is not None, "a forked campaign must outlive the recipe it came from"
        db.session.refresh(surviving)
        assert surviving.source_recipe_id is None, "the 'copied from' breadcrumb should be cleared"
