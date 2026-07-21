"""add lead_time_days to gift_catalog_items

Revision ID: b1c4e8a2f6d3
Revises: a3d7e9c1b5f2
Create Date: 2026-07-21 00:00:00.000000

"""
import random

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b1c4e8a2f6d3'
down_revision = 'a3d7e9c1b5f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('gift_catalog_items', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('lead_time_days', sa.Integer(), nullable=False, server_default='7')
        )

    # Backfill existing catalog items with a random lead time (5-10 days)
    # as a reasonable placeholder until each item is reviewed and given a
    # real value. Done row-by-row in Python rather than a single SQL
    # RAND() UPDATE so this behaves identically on sqlite (dev) and MySQL
    # (production).
    conn = op.get_bind()
    gift_catalog_items = sa.table(
        'gift_catalog_items',
        sa.column('id', sa.String),
        sa.column('lead_time_days', sa.Integer),
    )
    rows = conn.execute(sa.text('SELECT id FROM gift_catalog_items')).fetchall()
    for (item_id,) in rows:
        conn.execute(
            gift_catalog_items.update()
            .where(gift_catalog_items.c.id == item_id)
            .values(lead_time_days=random.randint(5, 10))
        )


def downgrade():
    with op.batch_alter_table('gift_catalog_items', schema=None) as batch_op:
        batch_op.drop_column('lead_time_days')
