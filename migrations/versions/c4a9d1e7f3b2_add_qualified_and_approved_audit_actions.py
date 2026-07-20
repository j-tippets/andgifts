"""add action_suggested and action_approved audit actions

Revision ID: c4a9d1e7f3b2
Revises: b7e1f3a2c9d4
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c4a9d1e7f3b2'
down_revision = 'b7e1f3a2c9d4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action',
            existing_type=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                'action_suggested', 'action_approved',
                name='contact_audit_action',
            ),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action',
            existing_type=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                'action_suggested', 'action_approved',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                name='contact_audit_action',
            ),
            existing_nullable=False,
        )
