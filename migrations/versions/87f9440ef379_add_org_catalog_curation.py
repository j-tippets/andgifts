"""add org catalog curation

Revision ID: 87f9440ef379
Revises: 260a6b043ec3
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '87f9440ef379'
down_revision = '260a6b043ec3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('catalog_curated', sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        'org_catalog_selections',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('gift_catalog_item_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['gift_catalog_item_id'], ['gift_catalog_items.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'gift_catalog_item_id', name='uq_org_catalog_selection'),
    )
    with op.batch_alter_table('org_catalog_selections', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_org_catalog_selections_org_id'), ['org_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_org_catalog_selections_gift_catalog_item_id'),
            ['gift_catalog_item_id'], unique=False,
        )


def downgrade():
    with op.batch_alter_table('org_catalog_selections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_org_catalog_selections_gift_catalog_item_id'))
        batch_op.drop_index(batch_op.f('ix_org_catalog_selections_org_id'))
    op.drop_table('org_catalog_selections')

    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_column('catalog_curated')
