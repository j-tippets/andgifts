"""add custom event types (milestones)

Revision ID: a3d7e9c1b5f2
Revises: f6c2b9a1e3d8
Create Date: 2026-07-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a3d7e9c1b5f2'
down_revision = 'f6c2b9a1e3d8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'custom_event_types',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('scope', sa.Enum('org', 'personal', name='custom_event_type_scope'), nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), nullable=True),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'key', name='uq_custom_event_type_key'),
    )
    with op.batch_alter_table('custom_event_types', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_custom_event_types_org_id'), ['org_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_custom_event_types_owner_user_id'), ['owner_user_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('custom_event_types', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_custom_event_types_owner_user_id'))
        batch_op.drop_index(batch_op.f('ix_custom_event_types_org_id'))
    op.drop_table('custom_event_types')
