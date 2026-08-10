"""add org event log

Revision ID: 07a722395150
Revises: a1b2c3d4e5f6
Create Date: 2026-08-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '07a722395150'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'org_event_log',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=True),
        sa.Column('org_name_snapshot', sa.String(length=255), nullable=False),
        sa.Column(
            'event_type',
            sa.Enum('signup', 'upgrade', 'downgrade', name='org_event_type'),
            nullable=False,
        ),
        sa.Column('from_tier', sa.String(length=20), nullable=True),
        sa.Column('to_tier', sa.String(length=20), nullable=False),
        sa.Column('email_delivered', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('org_event_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_org_event_log_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_org_event_log_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('org_event_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_org_event_log_created_at'))
        batch_op.drop_index(batch_op.f('ix_org_event_log_org_id'))
    op.drop_table('org_event_log')
