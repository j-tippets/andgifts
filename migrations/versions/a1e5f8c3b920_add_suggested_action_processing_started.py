"""add suggested_action processing_started_at

Revision ID: a1e5f8c3b920
Revises: b3d6a1f8c942
Create Date: 2026-09-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1e5f8c3b920'
down_revision = 'b3d6a1f8c942'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processing_started_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.drop_column('processing_started_at')
