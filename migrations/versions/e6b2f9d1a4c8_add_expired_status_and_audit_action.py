"""add expired suggestion status and action_expired audit action

Revision ID: e6b2f9d1a4c8
Revises: d3a8f1c4e7b2
Create Date: 2026-07-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e6b2f9d1a4c8'
down_revision = 'd3a8f1c4e7b2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(
                'pending', 'approved', 'skipped', 'sent', 'deleted',
                name='suggested_action_status',
            ),
            type_=sa.Enum(
                'pending', 'approved', 'skipped', 'sent', 'deleted', 'expired',
                name='suggested_action_status',
            ),
            existing_nullable=True,
        )

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
                'action_suggested', 'action_approved', 'action_unapproved',
                'action_expired',
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
                'action_expired',
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

    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(
                'pending', 'approved', 'skipped', 'sent', 'deleted', 'expired',
                name='suggested_action_status',
            ),
            type_=sa.Enum(
                'pending', 'approved', 'skipped', 'sent', 'deleted',
                name='suggested_action_status',
            ),
            existing_nullable=True,
        )
