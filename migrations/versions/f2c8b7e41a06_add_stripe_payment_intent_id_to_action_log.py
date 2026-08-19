"""add stripe_payment_intent_id to action_log

Revision ID: f2c8b7e41a06
Revises: e8f4a916c723
Create Date: 2026-08-20 00:00:00.000000

Traceability for automated flow-triggered gift charges (see
routes/dashboard.approve_action and services/payments.charge_saved_card)
-- lets a gift ActionLog row be matched back to the actual Stripe
PaymentIntent it charged, same idea as Order.stripe_payment_intent_id
for the manual order flow.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f2c8b7e41a06'
down_revision = 'e8f4a916c723'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("action_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("action_log", schema=None) as batch_op:
        batch_op.drop_column("stripe_payment_intent_id")
