"""merge heads (filler_action_states + onboarding_pending_invites)

Revision ID: a29f7c451b60
Revises: c3a9e7f14d02, d4b8f2c91a37
Create Date: 2026-08-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a29f7c451b60'
down_revision = ('c3a9e7f14d02', 'd4b8f2c91a37')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
