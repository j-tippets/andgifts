"""add stock_quantity to gift_catalog_items

Revision ID: 833216a599db
Revises: a1e5f8c3b920
Create Date: 2026-09-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '833216a599db'
down_revision = 'a1e5f8c3b920'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, no server default: NULL means "not tracked / unlimited"
    # (every existing item's current behavior), so this backfills nothing
    # -- every item stays orderable exactly as before until someone
    # explicitly sets a count in the admin catalog form. Only once a
    # number is set does the item become tracked and start blocking
    # orders at zero (see GiftCatalogItem.is_in_stock).
    with op.batch_alter_table('gift_catalog_items', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('stock_quantity', sa.Integer(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('gift_catalog_items', schema=None) as batch_op:
        batch_op.drop_column('stock_quantity')
