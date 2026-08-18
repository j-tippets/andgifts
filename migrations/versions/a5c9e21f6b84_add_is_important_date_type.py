"""add is_important_date_type to custom_event_types

Revision ID: a5c9e21f6b84
Revises: f7b2c4e9a163
Create Date: 2026-08-19 00:00:00.000000

Important Dates auto-create/reuse a CustomEventType behind the scenes
so a flow can still target a freeform label by event_type (see
routes/contacts._resolve_event_type_for_label). Nothing previously
distinguished those rows from a genuine, repeatable Timeline milestone
type an admin/agent deliberately added via Settings > Milestones, so
they were leaking into the Timeline's own "Event type" dropdown
(_visible_event_types) right alongside Closing/Showing/etc.

Adds is_important_date_type (Boolean, default False) to
custom_event_types. Deliberately NOT backfilled true for any existing
row: a personal-scope CustomEventType created via Important Dates and
one created by hand via Settings > Milestones are identical in shape
(same columns, same scope), so there's no reliable signal to tell them
apart after the fact -- guessing off the key (e.g. "not a known
practice-type milestone key") would misclassify any legitimately
custom Timeline milestone an agent already added by hand, hiding it
from their own dropdown. Every row created FROM THIS POINT ON is
tagged accurately at creation time; any already-stray rows (birthday-
style one-off labels that ended up in the Timeline dropdown before
this fix) can be deleted individually from Settings > Milestones.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a5c9e21f6b84'
down_revision = 'f7b2c4e9a163'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("custom_event_types", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "is_important_date_type", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade():
    with op.batch_alter_table("custom_event_types", schema=None) as batch_op:
        batch_op.drop_column("is_important_date_type")
