"""extend suggested_actions for campaign engine

Revision ID: 9f43802506b8
Revises: 6bd07f50048e
Create Date: 2026-07-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9f43802506b8'
down_revision = '6bd07f50048e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.alter_column(
            'action_type',
            existing_type=sa.Enum('gift', 'email', 'text', name='suggested_action_type'),
            type_=sa.Enum('gift', 'email', 'text', 'handwritten_note', name='suggested_action_type'),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column('source_campaign_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('generated_message', sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            'fk_suggested_actions_source_campaign', 'campaigns', ['source_campaign_id'], ['id']
        )
        batch_op.create_index(
            batch_op.f('ix_suggested_actions_source_campaign_id'), ['source_campaign_id'], unique=False
        )

    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action_type',
            existing_type=sa.Enum('gift', 'email', 'text', name='action_log_type'),
            type_=sa.Enum('gift', 'email', 'text', 'handwritten_note', name='action_log_type'),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.alter_column(
            'action_type',
            existing_type=sa.Enum('gift', 'email', 'text', 'handwritten_note', name='action_log_type'),
            type_=sa.Enum('gift', 'email', 'text', name='action_log_type'),
            existing_nullable=False,
        )

    with op.batch_alter_table('suggested_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_suggested_actions_source_campaign_id'))
        batch_op.drop_constraint('fk_suggested_actions_source_campaign', type_='foreignkey')
        batch_op.drop_column('generated_message')
        batch_op.drop_column('source_campaign_id')
        batch_op.alter_column(
            'action_type',
            existing_type=sa.Enum('gift', 'email', 'text', 'handwritten_note', name='suggested_action_type'),
            type_=sa.Enum('gift', 'email', 'text', name='suggested_action_type'),
            existing_nullable=False,
        )
