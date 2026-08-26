"""add filler_action_states

Revision ID: c3a9e7f14d02
Revises: b7e4c1f92a83
Create Date: 2026-08-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3a9e7f14d02'
down_revision = 'b7e4c1f92a83'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'filler_action_states',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('filler_key', sa.String(length=120), nullable=False),
        sa.Column('status', sa.Enum('dismissed', 'actioned', name='filler_action_status'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'filler_key', name='uq_filler_action_user_key'),
    )
    with op.batch_alter_table('filler_action_states', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_filler_action_states_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_filler_action_states_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_filler_action_states_filler_key'), ['filler_key'], unique=False)


def downgrade():
    with op.batch_alter_table('filler_action_states', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_filler_action_states_filler_key'))
        batch_op.drop_index(batch_op.f('ix_filler_action_states_user_id'))
        batch_op.drop_index(batch_op.f('ix_filler_action_states_org_id'))
    op.drop_table('filler_action_states')
