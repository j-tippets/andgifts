"""add important dates and currency support

Revision ID: d3e8a1c9f204
Revises: c8a4f102d6e9
Create Date: 2026-08-18 00:00:00.000000

Three additions, all requested together for the &Gifts custom-flow
work:

- TimelineEvent.is_important_date: flags a row as belonging to the
  Contact page's Important Dates card instead of the narrative
  Timeline feed. Still an ordinary recurring TimelineEvent underneath
  (routes force is_recurring=True/recurrence_rule="annual" alongside
  this flag), so the suggestion engine and campaign conditions need no
  changes -- this is purely a display split.
- TimelineEvent.year_known: lets an Important Date be saved with only
  a month/day (e.g. an unknown birth year) without lying about the
  year. event_date itself stays NOT NULL (recurrence math needs a real
  year to do .replace(year=...)); year_known just tells the UI whether
  that year is real or a placeholder.
- TimelineEvent.amount_cents: optional dollar amount on an event (e.g.
  a home purchase price), stored as integer cents. Feeds the new
  "event_amount" flow-condition field in campaign_rules.py.
- CustomFieldDefinition.field_type gains "currency" as a value,
  alongside the existing text/textarea/number/date/checkbox/select.
  Values are still stored as plain text on CustomFieldValue (same as
  "number" today) -- this migration only needs to widen the enum.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd3e8a1c9f204'
down_revision = 'c8a4f102d6e9'
branch_labels = None
depends_on = None


NEW_CUSTOM_FIELD_TYPES = ("text", "textarea", "number", "currency", "date", "checkbox", "select")
OLD_CUSTOM_FIELD_TYPES = ("text", "textarea", "number", "date", "checkbox", "select")


def upgrade():
    with op.batch_alter_table("timeline_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "is_important_date", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch_op.add_column(sa.Column(
            "year_known", sa.Boolean(), nullable=False, server_default=sa.true(),
        ))
        batch_op.add_column(sa.Column("amount_cents", sa.Integer(), nullable=True))

    # MySQL enums are altered in place via a raw MODIFY COLUMN -- there's
    # no data to migrate since "currency" is a brand-new option, so this
    # is safe to run directly rather than the create-new/copy/drop-old
    # dance a batch_alter_table would otherwise do for SQLite.
    op.alter_column(
        "custom_field_definitions", "field_type",
        existing_type=sa.Enum(*OLD_CUSTOM_FIELD_TYPES, name="custom_field_type"),
        type_=sa.Enum(*NEW_CUSTOM_FIELD_TYPES, name="custom_field_type"),
        existing_nullable=False,
    )


def downgrade():
    # Any field_definition rows already using "currency" would violate
    # the narrowed enum -- downgrade only makes sense if none exist,
    # same assumption every other enum-narrowing migration in this repo
    # makes.
    op.alter_column(
        "custom_field_definitions", "field_type",
        existing_type=sa.Enum(*NEW_CUSTOM_FIELD_TYPES, name="custom_field_type"),
        type_=sa.Enum(*OLD_CUSTOM_FIELD_TYPES, name="custom_field_type"),
        existing_nullable=False,
    )

    with op.batch_alter_table("timeline_events", schema=None) as batch_op:
        batch_op.drop_column("amount_cents")
        batch_op.drop_column("year_known")
        batch_op.drop_column("is_important_date")
