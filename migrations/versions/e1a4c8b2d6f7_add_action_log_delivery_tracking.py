"""add action_log delivery tracking

Revision ID: e1a4c8b2d6f7
Revises: d8f3b6a1c9e5
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e1a4c8b2d6f7'
down_revision = 'd8f3b6a1c9e5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'delivery_status',
            sa.Enum('sent', 'failed', name='action_log_delivery_status'),
            nullable=True,
        ))
        batch_op.add_column(sa.Column('delivery_error', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.drop_column('delivery_error')
        batch_op.drop_column('delivery_status')
