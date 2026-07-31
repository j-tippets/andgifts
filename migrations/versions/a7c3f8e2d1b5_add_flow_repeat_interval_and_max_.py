"""add flow repeat interval and max occurrences

Revision ID: a7c3f8e2d1b5
Revises: f2a9c7e1b4d8
Create Date: 2026-07-31 00:00:00.000000

repeat_enabled was previously just an on/off toggle riding on whatever
cadence the underlying TimelineEvent happened to recur on (e.g. every
year for a birthday). That doesn't work for a one-time event like a
closing date -- there's no natural recurrence to ride on, so a flow
built on it could never repeat at all.

recur_interval_amount/recur_interval_unit give the FLOW its own
repeat schedule ("every 1 year"), independent of whether the
underlying event recurs on its own. max_occurrences caps how many
times a flow will ever fire for the same contact, regardless of its
schedule (NULL = unlimited).

Reuses the existing campaign_timing_unit / live_campaign_timing_unit
enum types (day/week/month/year) rather than creating new ones, since
the value set is identical to timing_unit's.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a7c3f8e2d1b5'
down_revision = 'f2a9c7e1b4d8'
branch_labels = None
depends_on = None

TABLES = ("campaign_recipes", "campaigns")
UNIT_ENUMS = {
    "campaign_recipes": "campaign_timing_unit",
    "campaigns": "live_campaign_timing_unit",
}


def upgrade():
    for table in TABLES:
        # create_type=False -- these enum types already exist (created
        # by f4c7d2a9e1b6 for the timing_unit column); reuse them rather
        # than trying to CREATE TYPE a second time.
        unit_enum = sa.Enum("day", "week", "month", "year", name=UNIT_ENUMS[table], create_type=False)
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "recur_interval_amount", sa.Integer(), nullable=False, server_default="1",
            ))
            batch_op.add_column(sa.Column(
                "recur_interval_unit", unit_enum, nullable=False, server_default="year",
            ))
            batch_op.add_column(sa.Column("max_occurrences", sa.Integer(), nullable=True))


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("max_occurrences")
            batch_op.drop_column("recur_interval_unit")
            batch_op.drop_column("recur_interval_amount")
