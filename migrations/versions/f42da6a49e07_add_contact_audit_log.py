"""add contact audit log

Revision ID: f42da6a49e07
Revises: 93c7b9ac4595
Create Date: 2026-07-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f42da6a49e07'
down_revision = '93c7b9ac4595'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'contact_audit_log',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=True),
        sa.Column('contact_name_snapshot', sa.String(length=255), nullable=False),
        sa.Column('actor_user_id', sa.String(length=36), nullable=True),
        sa.Column('actor_name_snapshot', sa.String(length=150), nullable=False),
        sa.Column(
            'action',
            sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted',
                name='contact_audit_action',
            ),
            nullable=False,
        ),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contact_audit_log_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_audit_log_contact_id'), ['contact_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_audit_log_actor_user_id'), ['actor_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_audit_log_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_audit_log_created_at'))
        batch_op.drop_index(batch_op.f('ix_contact_audit_log_actor_user_id'))
        batch_op.drop_index(batch_op.f('ix_contact_audit_log_contact_id'))
        batch_op.drop_index(batch_op.f('ix_contact_audit_log_org_id'))
    op.drop_table('contact_audit_log')
