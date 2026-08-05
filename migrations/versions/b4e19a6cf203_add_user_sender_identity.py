"""add user sender identity (sendgrid single sender verification)

Revision ID: b4e19a6cf203
Revises: f9b3c7e2a815
Create Date: 2026-08-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b4e19a6cf203'
down_revision = 'f9b3c7e2a815'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sender_email', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('sender_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('sendgrid_sender_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            'sender_verified', sa.Boolean(), nullable=False, server_default=sa.false()
        ))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('sender_verified')
        batch_op.drop_column('sendgrid_sender_id')
        batch_op.drop_column('sender_name')
        batch_op.drop_column('sender_email')
