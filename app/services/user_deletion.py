"""Deleting a single User, completely.

Two routes delete a user: team.delete_member (an admin removing a
teammate) and profile.delete_account (someone closing their own account
while other users remain in the org). Both previously did their own
ad-hoc cleanup, both cleared the same three references
(Contact.owner_user_id, User.invited_by_user_id,
ContactAuditLog.actor_user_id), and both missed the same seven. The
schema has eleven FKs pointing at users.id with no ON DELETE behaviour,
so in production -- MySQL/InnoDB, which enforces them -- removing a
teammate who had done essentially anything raised IntegrityError and
500'd.

Having two copies of this logic is what let it drift and what made the
same bug get fixed nowhere twice. There is one copy now.

The delete/null decision per table, since it is a business call and not
a mechanical one:

  DELETED -- personal state that means nothing without its owner:
    payment_methods       a card belonging to a departed user must not
                          survive; automated flows charge saved cards,
                          and an ex-agent's card staying chargeable is
                          the worst failure mode in this whole file
    flow_recommendations  per-user suggestions, meaningless once the
                          user is gone
    filler_action_states  per-user dashboard dismissal state
    milestone_priorities  per-user milestone ranking

  NULLED -- org-level history and financial records that must outlive
  any individual user, all of which already carry name snapshots or
  belong to the org rather than the person:
    action_log.approved_by_user_id    spend/tax history
    orders.ordered_by_user_id         financial record
    campaigns.owner_user_id           org's flows keep working
    campaigns.created_by_user_id      authorship breadcrumb
    custom_field_definitions.owner_user_id
    custom_event_types.owner_user_id
    badges.owner_user_id
    contacts.owner_user_id            handled by the caller, which asks
                                      the user to confirm reassignment
                                      first
    contact_audit_log.actor_user_id   preserved via actor_name_snapshot
    users.invited_by_user_id          invite attribution

Deliberately NOT handled here: Stripe-side detachment of the deleted
payment methods. Deleting the local row stops this app from charging
the card, which is the urgent part; the Stripe PaymentMethod object is
left attached to the customer. Detaching it is a network call inside a
database transaction, which is exactly the pattern that produces
half-committed state on a timeout. It belongs in the job queue when
that exists (Fix-before-scale #16).
"""
from app.extensions import db
from app.models import (
    ActionLog,
    Badge,
    Campaign,
    Contact,
    ContactAuditLog,
    CustomEventType,
    CustomFieldDefinition,
    FillerActionState,
    FlowRecommendation,
    MilestonePriority,
    Order,
    PaymentMethod,
    User,
)
from app.services.storage import delete_avatar


def detach_and_delete_user(user, delete_photo=True):
    """Clears every reference to `user` and deletes the row.

    Does NOT commit -- the caller owns the transaction, so this can be
    composed with other work (and so a failure anywhere rolls back the
    whole thing rather than leaving a partially-detached user).

    Does NOT handle authorization, org-shape rules (last admin, sole
    user), or reassigning owned contacts. Callers do that first; by the
    time this is called, the decision to delete has been made.
    """
    user_id = user.id

    # --- Personal state: delete outright ---
    # Cards first and unconditionally. A departed user's saved card
    # remaining chargeable by automated flows is the one outcome here
    # with direct financial consequences.
    PaymentMethod.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    FlowRecommendation.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    FillerActionState.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    MilestonePriority.query.filter_by(user_id=user_id).delete(synchronize_session=False)

    # --- Org history and financial records: keep the row, drop the link ---
    ActionLog.query.filter_by(approved_by_user_id=user_id).update(
        {"approved_by_user_id": None}, synchronize_session=False
    )
    Order.query.filter_by(ordered_by_user_id=user_id).update(
        {"ordered_by_user_id": None}, synchronize_session=False
    )
    Campaign.query.filter_by(owner_user_id=user_id).update(
        {"owner_user_id": None}, synchronize_session=False
    )
    Campaign.query.filter_by(created_by_user_id=user_id).update(
        {"created_by_user_id": None}, synchronize_session=False
    )
    CustomFieldDefinition.query.filter_by(owner_user_id=user_id).update(
        {"owner_user_id": None}, synchronize_session=False
    )
    CustomEventType.query.filter_by(owner_user_id=user_id).update(
        {"owner_user_id": None}, synchronize_session=False
    )
    Badge.query.filter_by(owner_user_id=user_id).update(
        {"owner_user_id": None}, synchronize_session=False
    )
    # Audit history survives via actor_name_snapshot.
    ContactAuditLog.query.filter_by(actor_user_id=user_id).update(
        {"actor_user_id": None}, synchronize_session=False
    )
    # Self-referential invite attribution.
    User.query.filter_by(invited_by_user_id=user_id).update(
        {"invited_by_user_id": None}, synchronize_session=False
    )
    # Belt and braces: callers are expected to have handled owned
    # contacts (with a confirmation prompt, since it's a visible change
    # to the org's data), but an unhandled one must not turn into a 500.
    Contact.query.filter_by(owner_user_id=user_id).update(
        {"owner_user_id": None}, synchronize_session=False
    )

    photo_url = user.photo_url

    # Flush the detachments before the delete so the FK checks below see
    # the cleared references rather than the pre-update state.
    db.session.flush()
    # An ORM delete rather than a bulk .delete(): bulk deletes bypass
    # relationship cascades entirely, which is how profile.delete_account
    # managed to leave payment_methods behind even though
    # User.payment_methods declares cascade="all, delete-orphan".
    db.session.delete(user)
    db.session.flush()

    # Storage cleanup last and outside the FK work -- a failed avatar
    # delete must not roll back the account deletion. delete_avatar is
    # already best-effort internally.
    if delete_photo and photo_url:
        delete_avatar(photo_url)
