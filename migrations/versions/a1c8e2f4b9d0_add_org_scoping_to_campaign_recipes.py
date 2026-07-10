"""add org scoping to campaign recipes (global vs agency flows)

Revision ID: a1c8e2f4b9d0
Revises: 6f53c92d3337
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1c8e2f4b9d0'
down_revision = '6f53c92d3337'
branch_labels = None
depends_on = None


def _find_fk_name(inspector, table, referred_table, constrained_column):
    """The original campaigns.source_recipe_id FK was created without an
    explicit name (see 6bd07f50048e), so MySQL auto-named it (e.g.
    campaigns_ibfk_3) -- the exact suffix isn't reliable to hardcode, so
    look it up instead of guessing."""
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") == referred_table and constrained_column in fk.get("constrained_columns", []):
            return fk["name"]
    raise RuntimeError(
        f"Could not find the existing FK from {table}.{constrained_column} to {referred_table}.id "
        "-- check it manually before re-running this migration."
    )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    old_fk_name = _find_fk_name(inspector, "campaigns", "campaign_recipes", "source_recipe_id")

    with op.batch_alter_table('campaign_recipes', schema=None) as batch_op:
        # NULL = global flow (platform-authored, shown to every agency).
        # Set = local/agency flow (that org's own admin authored it, only
        # shown in that org's Flow Library).
        batch_op.add_column(sa.Column('org_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_campaign_recipes_org_id'), ['org_id'], unique=False)
        batch_op.create_foreign_key('fk_campaign_recipes_org', 'orgs', ['org_id'], ['id'])

    # Agency admins can now hard-delete a local recipe from the Flow
    # Library (global, platform-authored recipes still can't be deleted,
    # only deactivated -- this is just making the FK safe either way).
    # Campaigns already copied from a deleted recipe keep their own
    # copied fields; they just lose the "copied from" breadcrumb rather
    # than blocking the delete.
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_constraint(old_fk_name, type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_campaigns_source_recipe', 'campaign_recipes', ['source_recipe_id'], ['id'],
            ondelete='SET NULL',
        )


def downgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_constraint('fk_campaigns_source_recipe', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_campaigns_source_recipe_restore', 'campaign_recipes', ['source_recipe_id'], ['id']
        )

    with op.batch_alter_table('campaign_recipes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_campaign_recipes_org', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_campaign_recipes_org_id'))
        batch_op.drop_column('org_id')
