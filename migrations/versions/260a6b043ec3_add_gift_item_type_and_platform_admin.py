"""add gift item_type and user platform_admin

Revision ID: 260a6b043ec3
Revises: 0a7ebb345b12
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '260a6b043ec3'
down_revision = '0a7ebb345b12'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('gift_catalog_items', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'item_type',
                sa.Enum('product', 'service', name='gift_item_type'),
                nullable=False,
                server_default='product',
            )
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('platform_admin', sa.Boolean(), nullable=False, server_default=sa.false())
        )

    # Grant platform_admin to the single earliest-created user account, which
    # will be Jeremiah's own account on both the dev and production DBs. If
    # more platform operators are added later, flip this manually for them.
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE users SET platform_admin = true "
        "WHERE id = (SELECT id FROM (SELECT id FROM users ORDER BY created_at ASC LIMIT 1) AS t)"
    ))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('platform_admin')

    with op.batch_alter_table('gift_catalog_items', schema=None) as batch_op:
        batch_op.drop_column('item_type')
