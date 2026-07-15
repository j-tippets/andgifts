"""add orders table

Revision ID: 5c1e9a7d2b44
Revises: a1c8e2f4b9d0
Create Date: 2026-07-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5c1e9a7d2b44'
down_revision = 'a1c8e2f4b9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'orders',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=False),
        sa.Column('ordered_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('gift_catalog_item_id', sa.String(length=36), nullable=True),
        sa.Column('gift_name_snapshot', sa.String(length=255), nullable=False),
        sa.Column('gift_price_cents', sa.Integer(), nullable=False),
        sa.Column('fulfillment_method', sa.Enum('shipping', 'pickup', name='order_fulfillment_method'), nullable=False),
        sa.Column('pickup_location', sa.String(length=255), nullable=True),
        sa.Column('shipping_cost_cents', sa.Integer(), nullable=False),
        sa.Column('shipping_address_snapshot', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'paid', 'fulfilled', 'cancelled', name='order_status'), nullable=False),
        sa.Column('stripe_checkout_session_id', sa.String(length=255), nullable=True),
        sa.Column('stripe_payment_intent_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['ordered_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['gift_catalog_item_id'], ['gift_catalog_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_contact_id'), ['contact_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_status'), ['status'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_orders_stripe_checkout_session_id'), ['stripe_checkout_session_id'], unique=False
        )

    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action',
            existing_type=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                name='contact_audit_action',
            ),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('contact_audit_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action',
            existing_type=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted', 'gift_ordered',
                name='contact_audit_action',
            ),
            type_=sa.Enum(
                'created', 'updated', 'status_changed', 'reassigned',
                'timeline_added', 'timeline_updated', 'deleted',
                name='contact_audit_action',
            ),
            existing_nullable=False,
        )

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_stripe_checkout_session_id'))
        batch_op.drop_index(batch_op.f('ix_orders_status'))
        batch_op.drop_index(batch_op.f('ix_orders_contact_id'))
        batch_op.drop_index(batch_op.f('ix_orders_org_id'))
    op.drop_table('orders')
