"""add photo_url to contacts

Revision ID: 22a5f9a13ec4
Revises: f0540e8126d6
Create Date: 2026-07-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '22a5f9a13ec4'
down_revision = 'f0540e8126d6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('contacts', sa.Column('photo_url', sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column('contacts', 'photo_url')
