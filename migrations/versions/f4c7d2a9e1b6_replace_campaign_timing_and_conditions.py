"""replace campaign offset_days/interest_tag with timing fields

Revision ID: f4c7d2a9e1b6
Revises: e6b2f9d1a4c8
Create Date: 2026-07-23 00:00:00.000000

No live flows exist yet in production, so this is a straight
drop-and-add rather than a data-preserving migration.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f4c7d2a9e1b6'
down_revision = 'e6b2f9d1a4c8'
branch_labels = None
depends_on = None

TABLES = ("campaign_recipes", "campaigns")
DIRECTION_ENUMS = {
    "campaign_recipes": "campaign_timing_direction",
    "campaigns": "live_campaign_timing_direction",
}
UNIT_ENUMS = {
    "campaign_recipes": "campaign_timing_unit",
    "campaigns": "live_campaign_timing_unit",
}


def upgrade():
    for table in TABLES:
        direction_enum = sa.Enum("before", "same_day", "after", name=DIRECTION_ENUMS[table])
        unit_enum = sa.Enum("day", "week", "month", "year", name=UNIT_ENUMS[table])
        direction_enum.create(op.get_bind(), checkfirst=True)
        unit_enum.create(op.get_bind(), checkfirst=True)

        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("offset_days")
            batch_op.drop_column("interest_tag")
            batch_op.add_column(sa.Column(
                "timing_direction", direction_enum, nullable=False, server_default="after",
            ))
            batch_op.add_column(sa.Column(
                "timing_amount", sa.Integer(), nullable=False, server_default="1",
            ))
            batch_op.add_column(sa.Column(
                "timing_unit", unit_enum, nullable=False, server_default="day",
            ))
            batch_op.add_column(sa.Column(
                "repeat_enabled", sa.Boolean(), nullable=False, server_default=sa.true(),
            ))

    # rule_type now holds condition field keys (e.g. "custom:<uuid>" for
    # an org's own custom fields), which run longer than the old fixed
    # rule-type names -- widen to fit.
    for rule_table in ("campaign_recipe_rules", "campaign_rules"):
        with op.batch_alter_table(rule_table, schema=None) as batch_op:
            batch_op.alter_column(
                "rule_type", existing_type=sa.String(length=50),
                type_=sa.String(length=80), existing_nullable=False,
            )


def downgrade():
    for rule_table in ("campaign_recipe_rules", "campaign_rules"):
        with op.batch_alter_table(rule_table, schema=None) as batch_op:
            batch_op.alter_column(
                "rule_type", existing_type=sa.String(length=80),
                type_=sa.String(length=50), existing_nullable=False,
            )

    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("repeat_enabled")
            batch_op.drop_column("timing_unit")
            batch_op.drop_column("timing_amount")
            batch_op.drop_column("timing_direction")
            batch_op.add_column(sa.Column("interest_tag", sa.String(length=100), nullable=True))
            batch_op.add_column(sa.Column("offset_days", sa.Integer(), nullable=False, server_default="0"))

        sa.Enum(name=UNIT_ENUMS[table]).drop(op.get_bind(), checkfirst=True)
        sa.Enum(name=DIRECTION_ENUMS[table]).drop(op.get_bind(), checkfirst=True)
