"""add blocked to action_log_delivery_status

Revision ID: a1b2c3d4e5f6
Revises: c7d2f4a91e60
Create Date: 2026-08-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'c7d2f4a91e60'
branch_labels = None
depends_on = None


def upgrade():
    # 'blocked' is distinct from 'failed': failed means we tried to send
    # and SendGrid rejected it (an infra/deliverability problem to dig
    # into); blocked means we deliberately didn't try, because this org
    # hit its plan's monthly send cap or this contact is inside the
    # per-contact cooldown window. Same downstream UX (agent follows up
    # manually) but a very different remediation story, so it needs its
    # own value rather than overloading 'failed'.
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.alter_column(
            'delivery_status',
            existing_type=sa.Enum('sent', 'failed', name='action_log_delivery_status'),
            type_=sa.Enum('sent', 'failed', 'blocked', name='action_log_delivery_status'),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.alter_column(
            'delivery_status',
            existing_type=sa.Enum('sent', 'failed', 'blocked', name='action_log_delivery_status'),
            type_=sa.Enum('sent', 'failed', name='action_log_delivery_status'),
            existing_nullable=True,
        )
