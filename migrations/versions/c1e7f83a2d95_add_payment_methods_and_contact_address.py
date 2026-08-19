"""add saved payment methods and contact shipping address

Revision ID: c1e7f83a2d95
Revises: a5c9e21f6b84
Create Date: 2026-08-20 00:00:00.000000

Foundation for the in-app gift checkout redesign (replacing the
per-order Stripe Checkout redirect with a saved card charged directly)
and for automated flow-triggered gifts actually charging someone at
approval time, which today they don't at all.

- users.stripe_customer_id: each agent's own Stripe Customer for gift
  payments, separate from orgs.stripe_customer_id (subscription
  billing). Created lazily on first card add, not at signup.
- payment_methods: one row per saved card. is_default marks which
  card automated approvals charge without asking -- there's no
  "pick a card" moment on a plain Approve click.
- contacts gains a shipping address (one per household, not an address
  book) -- editable independently like any other contact field, not
  just set once during checkout.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c1e7f83a2d95'
down_revision = 'a5c9e21f6b84'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))

    op.create_table(
        "payment_methods",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("stripe_payment_method_id", sa.String(length=255), nullable=False),
        sa.Column("card_brand", sa.String(length=30), nullable=True),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("card_exp_month", sa.Integer(), nullable=True),
        sa.Column("card_exp_year", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_payment_method_id"),
    )
    with op.batch_alter_table("payment_methods", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_payment_methods_user_id"), ["user_id"], unique=False,
        )

    with op.batch_alter_table("contacts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("shipping_address_line1", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("shipping_address_line2", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("shipping_city", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("shipping_state", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("shipping_zip", sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table("contacts", schema=None) as batch_op:
        batch_op.drop_column("shipping_zip")
        batch_op.drop_column("shipping_state")
        batch_op.drop_column("shipping_city")
        batch_op.drop_column("shipping_address_line2")
        batch_op.drop_column("shipping_address_line1")

    with op.batch_alter_table("payment_methods", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_payment_methods_user_id"))
    op.drop_table("payment_methods")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("stripe_customer_id")
