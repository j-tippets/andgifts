"""add milestone priorities

Revision ID: 3e91c6b4a207
Revises: 07a722395150
Create Date: 2026-08-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3e91c6b4a207'
down_revision = '07a722395150'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'milestone_priorities',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'event_type', name='uq_milestone_priority_user_event'),
    )
    with op.batch_alter_table('milestone_priorities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_milestone_priorities_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('milestone_priorities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_milestone_priorities_user_id'))
    op.drop_table('milestone_priorities')
