"""add flow_recommendations

Revision ID: 230208531e85
Revises: f2c8b7e41a06
Create Date: 2026-08-19 00:00:00.000000

New "you might want a flow for this" cards on the Today dashboard --
see app/models/actions.py:FlowRecommendation and
app/services/flow_recommendations.py.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '230208531e85'
down_revision = 'f2c8b7e41a06'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'flow_recommendations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('event_label', sa.String(length=100), nullable=False),
        sa.Column('contact_count', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'accepted', 'dismissed', name='flow_recommendation_status'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'event_type', name='uq_flow_recommendation_user_event'),
    )
    with op.batch_alter_table('flow_recommendations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_flow_recommendations_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_flow_recommendations_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_flow_recommendations_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('flow_recommendations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_flow_recommendations_status'))
        batch_op.drop_index(batch_op.f('ix_flow_recommendations_user_id'))
        batch_op.drop_index(batch_op.f('ix_flow_recommendations_org_id'))
    op.drop_table('flow_recommendations')
