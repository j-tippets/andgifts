"""add processing status to suggested_actions

Revision ID: 3c1ed4ca2ec9
Revises: d42dce6fd934
Create Date: 2026-09-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3c1ed4ca2ec9'
down_revision = 'd42dce6fd934'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(
                'pending', 'approved', 'skipped', 'sent', 'deleted', 'expired',
                name='suggested_action_status',
            ),
            type_=sa.Enum(
                'pending', 'processing', 'approved', 'skipped', 'sent', 'deleted', 'expired',
                name='suggested_action_status',
            ),
            existing_nullable=True,
            existing_server_default='pending',
        )


def downgrade():
    # Any row caught mid-approval (status='processing') at the moment of
    # a downgrade gets reverted to 'pending' first -- the old enum has no
    # slot for it, and leaving it as 'processing' would make the
    # subsequent ALTER TABLE fail outright on that row.
    op.execute("UPDATE suggested_actions SET status = 'pending' WHERE status = 'processing'")
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(
                'pending', 'processing', 'approved', 'skipped', 'sent', 'deleted', 'expired',
                name='suggested_action_status',
            ),
            type_=sa.Enum(
                'pending', 'approved', 'skipped', 'sent', 'deleted', 'expired',
                name='suggested_action_status',
            ),
            existing_nullable=True,
            existing_server_default='pending',
        )
