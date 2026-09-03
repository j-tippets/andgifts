"""add suggested_action campaign occurrence uniqueness

Revision ID: d42dce6fd934
Revises: 399ceeb66244
Create Date: 2026-09-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd42dce6fd934'
down_revision = '399ceeb66244'
branch_labels = None
depends_on = None


def upgrade():
    # generate_campaign_suggestions_for_org's dedup check
    # (_campaign_suggestion_exists) was a check-then-insert race with no
    # DB backstop: two near-simultaneous runs (two gunicorn workers, or
    # the nightly job overlapping an on-demand dashboard-triggered run)
    # could both pass the check before either committed, producing two
    # suggestions for the same org+campaign+contact+event+date. Adding
    # the unique constraint below closes that going forward, but any
    # duplicates already sitting in the table from before this fix would
    # make the ALTER TABLE itself fail -- so clean those up first.
    #
    # Two categories of "safe to delete", handled differently:
    #
    # 1. Never approved at all (no ActionLog row) -- always safe to just
    #    remove outright, regardless of action_type.
    #
    # 2. Approved, but for a non-paid action_type (email, text) -- no
    #    Stripe charge is EVER involved for these (ActionLog.cost_cents
    #    and .stripe_payment_intent_id are always NULL for them; see
    #    ActionLog.stripe_payment_intent_id's own docstring), so unlike
    #    gift/handwritten_note there's no financial transaction at stake
    #    in removing the later duplicate's SuggestedAction row. Its
    #    ActionLog stays -- append-only, same principle as ever, and it
    #    genuinely happened (the email really was sent) -- just detached
    #    from the suggestion being removed by nulling
    #    suggested_action_id, rather than the row being deleted.
    #
    # A duplicate pair that's approved AND for a paid action_type (gift,
    # handwritten_note) is left completely untouched by both categories
    # above, and still makes the constraint creation below fail loudly
    # -- that's a real, separate problem (a client actually
    # double-charged/double-gifted) that deserves manual review, not a
    # migration-time decision either way.
    connection = op.get_bind()

    unsent_victim_ids = [
        row[0] for row in connection.execute(sa.text("""
            SELECT t1.id FROM suggested_actions t1
            INNER JOIN suggested_actions t2
              ON t1.org_id = t2.org_id
              AND t1.source_campaign_id = t2.source_campaign_id
              AND t1.contact_id = t2.contact_id
              AND t1.triggering_event_id = t2.triggering_event_id
              AND t1.target_date = t2.target_date
            WHERE t1.source_campaign_id IS NOT NULL
              AND t1.triggering_event_id IS NOT NULL
              AND (t1.created_at > t2.created_at
                   OR (t1.created_at = t2.created_at AND t1.id > t2.id))
              AND NOT EXISTS (
                  SELECT 1 FROM action_log
                  WHERE action_log.suggested_action_id = t1.id
              )
        """))
    ]

    sent_non_paid_victim_ids = [
        row[0] for row in connection.execute(sa.text("""
            SELECT t1.id FROM suggested_actions t1
            INNER JOIN suggested_actions t2
              ON t1.org_id = t2.org_id
              AND t1.source_campaign_id = t2.source_campaign_id
              AND t1.contact_id = t2.contact_id
              AND t1.triggering_event_id = t2.triggering_event_id
              AND t1.target_date = t2.target_date
            WHERE t1.source_campaign_id IS NOT NULL
              AND t1.triggering_event_id IS NOT NULL
              AND t1.action_type NOT IN ('gift', 'handwritten_note')
              AND (t1.created_at > t2.created_at
                   OR (t1.created_at = t2.created_at AND t1.id > t2.id))
              AND EXISTS (
                  SELECT 1 FROM action_log
                  WHERE action_log.suggested_action_id = t1.id
              )
        """))
    ]

    if sent_non_paid_victim_ids:
        null_action_log_fk = sa.text(
            "UPDATE action_log SET suggested_action_id = NULL WHERE suggested_action_id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True))
        connection.execute(null_action_log_fk, {"ids": sent_non_paid_victim_ids})

    victim_ids = unsent_victim_ids + sent_non_paid_victim_ids

    if victim_ids:
        delete_audit = sa.text(
            "DELETE FROM contact_audit_log WHERE suggested_action_id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True))
        connection.execute(delete_audit, {"ids": victim_ids})

        delete_actions = sa.text(
            "DELETE FROM suggested_actions WHERE id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True))
        connection.execute(delete_actions, {"ids": victim_ids})

    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_suggested_action_campaign_occurrence',
            ['org_id', 'source_campaign_id', 'contact_id', 'triggering_event_id', 'target_date'],
        )


def downgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_suggested_action_campaign_occurrence', type_='unique')
    # The duplicate rows (and their ContactAuditLog entries) removed in
    # upgrade() are not restored, and ActionLog rows that had their
    # suggested_action_id nulled out stay that way -- this is a data
    # cleanup, not just a schema change, and there's no record of
    # exactly what was changed to restore it.
