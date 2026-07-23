"""add support requests table

Revision ID: d3a8f1c4e7b2
Revises: c7f2a4e9b1d6
Create Date: 2026-07-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd3a8f1c4e7b2'
down_revision = 'c7f2a4e9b1d6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'support_requests',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=True),
        sa.Column('org_name_snapshot', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('user_name_snapshot', sa.String(length=150), nullable=False),
        sa.Column('user_email_snapshot', sa.String(length=255), nullable=False),
        sa.Column('topic', sa.String(length=64), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('email_delivered', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('support_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_support_requests_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_support_requests_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_support_requests_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('support_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_support_requests_created_at'))
        batch_op.drop_index(batch_op.f('ix_support_requests_user_id'))
        batch_op.drop_index(batch_op.f('ix_support_requests_org_id'))
    op.drop_table('support_requests')
