"""add dropoff fulfillment option

Revision ID: 7256d2597501
Revises: 5c1e9a7d2b44
Create Date: 2026-07-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '7256d2597501'
down_revision = '5c1e9a7d2b44'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'orgs',
        sa.Column('dropoff_enabled', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column('orgs', sa.Column('office_address', sa.String(length=255), nullable=True))

    op.add_column('orders', sa.Column('dropoff_location', sa.String(length=255), nullable=True))
    op.alter_column(
        'orders',
        'fulfillment_method',
        existing_type=sa.Enum('shipping', 'pickup', name='order_fulfillment_method'),
        type_=sa.Enum('shipping', 'pickup', 'dropoff', name='order_fulfillment_method'),
        existing_nullable=False,
    )


def downgrade():
    # Any existing 'dropoff' rows must be reassigned before narrowing the
    # enum back, or this MODIFY will fail/truncate on MySQL.
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE orders SET fulfillment_method = 'pickup' WHERE fulfillment_method = 'dropoff'"
    ))
    op.alter_column(
        'orders',
        'fulfillment_method',
        existing_type=sa.Enum('shipping', 'pickup', 'dropoff', name='order_fulfillment_method'),
        type_=sa.Enum('shipping', 'pickup', name='order_fulfillment_method'),
        existing_nullable=False,
    )
    op.drop_column('orders', 'dropoff_location')

    op.drop_column('orgs', 'office_address')
    op.drop_column('orgs', 'dropoff_enabled')
