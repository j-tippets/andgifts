"""add timeline_deleted audit action

Revision ID: f9b3c7e2a815
Revises: a7c3f8e2d1b5
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f9b3c7e2a815'
down_revision = 'a7c3f8e2d1b5'
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
                'action_suggested', 'action_approved', 'action_unapproved',
                'action_expired',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                'action_suggested', 'action_approved', 'action_unapproved',
                'action_expired', 'timeline_deleted',
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
                'action_expired', 'timeline_deleted',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                'action_deleted', 'action_undeleted',
                'action_suggested', 'action_approved', 'action_unapproved',
                'action_expired',
                name='contact_audit_action',
            ),
            existing_nullable=False,
        )
