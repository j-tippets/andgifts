"""add org onboarding_pending_invites

Revision ID: d4b8f2c91a37
Revises: b7e4c1f92a83
Create Date: 2026-08-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4b8f2c91a37'
down_revision = 'b7e4c1f92a83'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('onboarding_pending_invites', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_column('onboarding_pending_invites')
