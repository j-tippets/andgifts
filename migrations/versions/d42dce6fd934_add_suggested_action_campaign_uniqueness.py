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
    # Deliberately conservative about what counts as "safe to delete":
    # only rows with no ActionLog record pointing at them (i.e. never
    # actually approved/charged/sent) are candidates, and among those we
    # always keep the earliest (by created_at, tying on id) and remove
    # the rest. A duplicate pair that both have an ActionLog entry (two
    # real approvals of the same occurrence) is left untouched and will
    # make the constraint creation below fail loudly -- that's a real,
    # separate problem (a client double-charged/double-gifted) that
    # deserves manual review, not a silent migration-time deletion.
    op.execute("""
        DELETE t1 FROM suggested_actions t1
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
    """)

    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_suggested_action_campaign_occurrence',
            ['org_id', 'source_campaign_id', 'contact_id', 'triggering_event_id', 'target_date'],
        )


def downgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_suggested_action_campaign_occurrence', type_='unique')
    # The duplicate rows removed in upgrade() are not restored -- this is
    # a data cleanup, not just a schema change, and there's no record of
    # exactly which rows were deleted to bring back.
