"""add payment_method_id to orders

Revision ID: e8f4a916c723
Revises: c1e7f83a2d95
Create Date: 2026-08-20 00:00:00.000000

Part of moving the one-off gift order flow off Stripe Checkout onto a
saved card charged directly -- records which PaymentMethod an order
was charged on.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e8f4a916c723'
down_revision = 'c1e7f83a2d95'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("payment_method_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_orders_payment_method_id", "payment_methods", ["payment_method_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_constraint("fk_orders_payment_method_id", type_="foreignkey")
        batch_op.drop_column("payment_method_id")
