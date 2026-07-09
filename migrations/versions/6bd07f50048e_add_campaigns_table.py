"""add campaigns table

Revision ID: 6bd07f50048e
Revises: 3aa1eb963b3e
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6bd07f50048e'
down_revision = '3aa1eb963b3e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'campaigns',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), nullable=True),
        sa.Column('source_recipe_id', sa.String(length=36), nullable=True),
        sa.Column('created_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('offset_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('interest_tag', sa.String(length=100), nullable=True),
        sa.Column('price_max_cents', sa.Integer(), nullable=True),
        sa.Column('use_llm_gift_selection', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            'action_type',
            sa.Enum('gift', 'email', 'text', 'handwritten_note', name='live_campaign_action_type'),
            nullable=False,
        ),
        sa.Column('suggested_gift_id', sa.String(length=36), nullable=True),
        sa.Column('use_llm_copy', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('message_template', sa.Text(), nullable=True),
        sa.Column('llm_prompt_hint', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['source_recipe_id'], ['campaign_recipes.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['suggested_gift_id'], ['gift_catalog_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_campaigns_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_campaigns_owner_user_id'), ['owner_user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_campaigns_owner_user_id'))
        batch_op.drop_index(batch_op.f('ix_campaigns_org_id'))
    op.drop_table('campaigns')
