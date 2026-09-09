"""add processing status and claim timestamp to orders

Revision ID: c8a3f61d0e57
Revises: b7f4d02e9c31
Create Date: 2026-09-09 00:00:00.000000

Gives orders the same claim state suggested_actions already has (see
migration 3c1ed4ca2ec9), so routes/orders.confirm_order can take an
atomic UPDATE ... WHERE status='pending' lock before touching Stripe
instead of a read-then-write that two concurrent requests can both win.

"processing" means: a charge for this order has been started and its
outcome is not yet known. It is a transient state -- every clean path
leaves it within one Stripe round trip, either to "paid" or back to
"pending". processing_started_at exists so the reconciler can tell an
abandoned claim (crashed mid-charge) from one that is merely slow.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c8a3f61d0e57'
down_revision = 'b7f4d02e9c31'
branch_labels = None
depends_on = None

OLD_STATUSES = ('pending', 'paid', 'fulfilled', 'cancelled')
NEW_STATUSES = ('pending', 'processing', 'paid', 'fulfilled', 'cancelled')


def upgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processing_started_at', sa.DateTime(), nullable=True))
        # Batch mode: on MySQL this compiles to the same plain MODIFY
        # COLUMN an unbatched alter would emit, but it also stays
        # runnable on SQLite so the migration chain can be replayed for
        # tests/test_schema_matches_migrations.py.
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(*OLD_STATUSES, name='order_status'),
            type_=sa.Enum(*NEW_STATUSES, name='order_status'),
            existing_nullable=False,
        )


def downgrade():
    # "processing" is transient by design, but a row can be sitting in
    # it at the moment of a downgrade (a claim in flight, or one the
    # reconciler hasn't swept yet). Reverting those to "pending" is the
    # same recovery the reconciler performs and is safe for the same
    # reason: the charge carries a deterministic idempotency key, so a
    # retry cannot double-charge.
    op.get_bind().execute(
        sa.text("UPDATE orders SET status = 'pending' WHERE status = 'processing'")
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(*NEW_STATUSES, name='order_status'),
            type_=sa.Enum(*OLD_STATUSES, name='order_status'),
            existing_nullable=False,
        )
        batch_op.drop_column('processing_started_at')
