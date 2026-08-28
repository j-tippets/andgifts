"""add org trial_ends_at

Revision ID: e7c92d5f1a48
Revises: a29f7c451b60
Create Date: 2026-08-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e7c92d5f1a48'
down_revision = 'a29f7c451b60'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trial_ends_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_column('trial_ends_at')
