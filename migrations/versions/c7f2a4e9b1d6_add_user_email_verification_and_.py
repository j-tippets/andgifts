"""add user email verification and password reset fields

Revision ID: c7f2a4e9b1d6
Revises: b1c4e8a2f6d3
Create Date: 2026-07-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c7f2a4e9b1d6'
down_revision = 'b1c4e8a2f6d3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Defaults to true so every existing account (and every other
        # creation path -- team invite accept, admin direct-add) stays
        # exactly as trusted as it is today. Only self-registration sets
        # this false explicitly, in app code.
        batch_op.add_column(sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('email_verify_token', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('email_verify_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('reset_token', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('reset_expires_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_email_verify_token'), ['email_verify_token'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_reset_token'), ['reset_token'], unique=True)


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_reset_token'))
        batch_op.drop_index(batch_op.f('ix_users_email_verify_token'))
        batch_op.drop_column('reset_expires_at')
        batch_op.drop_column('reset_token')
        batch_op.drop_column('email_verify_expires_at')
        batch_op.drop_column('email_verify_token')
        batch_op.drop_column('email_verified')
