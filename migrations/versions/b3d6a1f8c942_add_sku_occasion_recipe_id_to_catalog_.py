"""add sku/occasion/recipe_id to gift catalog items and order snapshots

Revision ID: b3d6a1f8c942
Revises: 3c1ed4ca2ec9
Create Date: 2026-09-03 00:00:00.000000

Carries the SKU, occasion/collection, and internal fulfillment recipe id
from the external planning spreadsheet (andgifts_v1_catalog.xlsx) into
the app, so WDF's fulfillment notice (email + webhook) can include them
instead of just the gift name and an internal catalog item id. Order
gets its own snapshot columns for the same reason gift_name_snapshot and
gift_price_cents already exist there -- so a notice stays accurate even
if the catalog item is later edited or deleted.

All new columns are nullable: existing gift_catalog_items rows and
existing orders predate these fields and have nothing to backfill them
with.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b3d6a1f8c942'
down_revision = '3c1ed4ca2ec9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("gift_catalog_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sku", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("occasion", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("recipe_id", sa.String(length=20), nullable=True))

    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sku_snapshot", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("occasion_snapshot", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("recipe_id_snapshot", sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("recipe_id_snapshot")
        batch_op.drop_column("occasion_snapshot")
        batch_op.drop_column("sku_snapshot")

    with op.batch_alter_table("gift_catalog_items", schema=None) as batch_op:
        batch_op.drop_column("recipe_id")
        batch_op.drop_column("occasion")
        batch_op.drop_column("sku")
