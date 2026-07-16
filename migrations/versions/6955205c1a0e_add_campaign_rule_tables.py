"""add campaign rule tables

Revision ID: 6955205c1a0e
Revises: 7256d2597501
Create Date: 2026-07-16 00:00:00.000000

NOTE ON MERGE ORDER: this chains after 7256d2597501 (the office-dropoff
migration, still on its own unmerged branch at authoring time). If that
branch merges after this one, this migration will need its
down_revision bumped to whatever actually lands as head first --
otherwise `flask db upgrade` will see two migrations both claiming the
same down_revision and refuse to run (multiple heads).

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6955205c1a0e'
down_revision = '7256d2597501'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'campaign_recipe_rules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('recipe_id', sa.String(length=36), nullable=False),
        sa.Column('rule_type', sa.String(length=50), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['recipe_id'], ['campaign_recipes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_campaign_recipe_rules_recipe_id'), 'campaign_recipe_rules', ['recipe_id'],
    )

    op.create_table(
        'campaign_rules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('campaign_id', sa.String(length=36), nullable=False),
        sa.Column('rule_type', sa.String(length=50), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_campaign_rules_campaign_id'), 'campaign_rules', ['campaign_id'],
    )


def downgrade():
    op.drop_index(op.f('ix_campaign_rules_campaign_id'), table_name='campaign_rules')
    op.drop_table('campaign_rules')
    op.drop_index(op.f('ix_campaign_recipe_rules_recipe_id'), table_name='campaign_recipe_rules')
    op.drop_table('campaign_recipe_rules')
