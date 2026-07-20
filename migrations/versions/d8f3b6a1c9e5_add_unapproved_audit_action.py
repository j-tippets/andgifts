"""add action_unapproved audit action

Revision ID: d8f3b6a1c9e5
Revises: c4a9d1e7f3b2
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd8f3b6a1c9e5'
down_revision = 'c4a9d1e7f3b2'
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
                'action_suggested', 'action_approved',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                'action_suggested', 'action_approved', 'action_unapproved',
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
                'action_suggested', 'action_approved', 'action_unapproved',
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
