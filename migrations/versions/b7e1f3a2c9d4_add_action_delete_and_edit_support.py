"""add action delete/undelete support

Revision ID: b7e1f3a2c9d4
Revises: 22a5f9a13ec4
Create Date: 2026-07-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7e1f3a2c9d4'
down_revision = '22a5f9a13ec4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum('pending', 'approved', 'skipped', 'sent', name='suggested_action_status'),
            type_=sa.Enum('pending', 'approved', 'skipped', 'sent', 'deleted', name='suggested_action_status'),
            existing_nullable=True,
        )

    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action',
            existing_type=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
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
        batch_op.add_column(sa.Column('suggested_action_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_contact_audit_log_suggested_action', 'suggested_actions', ['suggested_action_id'], ['id']
        )
        batch_op.create_index(
            batch_op.f('ix_contact_audit_log_suggested_action_id'), ['suggested_action_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_audit_log_suggested_action_id'))
        batch_op.drop_constraint('fk_contact_audit_log_suggested_action', type_='foreignkey')
        batch_op.drop_column('suggested_action_id')
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
                name='contact_audit_action',
            ),
            existing_nullable=False,
        )

    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum('pending', 'approved', 'skipped', 'sent', 'deleted', name='suggested_action_status'),
            type_=sa.Enum('pending', 'approved', 'skipped', 'sent', name='suggested_action_status'),
            existing_nullable=True,
        )
