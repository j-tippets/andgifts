"""add contact do_not_contact and marketing_opt_out

Revision ID: 0a7ebb345b12
Revises: f42da6a49e07
Create Date: 2026-07-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0a7ebb345b12'
down_revision = 'f42da6a49e07'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('marketing_opt_out', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column('do_not_contact', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade():
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_column('do_not_contact')
        batch_op.drop_column('marketing_opt_out')
