"""add campaign forking and delete safety

Revision ID: 6f53c92d3337
Revises: 9f43802506b8
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6f53c92d3337'
down_revision = '9f43802506b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('forked_from_campaign_id', sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_campaigns_forked_from_campaign_id'), ['forked_from_campaign_id'], unique=False
        )
        # Self-referential: deleting the team-wide source flow just clears
        # this breadcrumb on any personal copies forked from it -- it never
        # blocks the delete and never cascades into deleting the copies.
        batch_op.create_foreign_key(
            'fk_campaigns_forked_from_campaign', 'campaigns', ['forked_from_campaign_id'], ['id'],
            ondelete='SET NULL',
        )

    # A campaign (team-wide or personal) can now be hard-deleted. Existing
    # suggested_actions.source_campaign_id pointed at it should survive as
    # history with the link cleared, rather than blocking the delete or
    # being dragged along with it -- so swap the FK from its default
    # (RESTRICT) to SET NULL.
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_suggested_actions_source_campaign', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_suggested_actions_source_campaign', 'campaigns', ['source_campaign_id'], ['id'],
            ondelete='SET NULL',
        )


def downgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_suggested_actions_source_campaign', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_suggested_actions_source_campaign', 'campaigns', ['source_campaign_id'], ['id']
        )

    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_constraint('fk_campaigns_forked_from_campaign', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_campaigns_forked_from_campaign_id'))
        batch_op.drop_column('forked_from_campaign_id')
