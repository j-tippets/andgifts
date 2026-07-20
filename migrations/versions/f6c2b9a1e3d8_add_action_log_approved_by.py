"""add action_log approved_by_user_id

Revision ID: f6c2b9a1e3d8
Revises: e1a4c8b2d6f7
Create Date: 2026-07-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f6c2b9a1e3d8'
down_revision = 'e1a4c8b2d6f7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.add_column(sa.Column('approved_by_user_id', sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_action_log_approved_by_user_id'), ['approved_by_user_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_action_log_approved_by_user', 'users', ['approved_by_user_id'], ['id'],
        )

    # Backfill from ContactAuditLog: every existing "approved" ActionLog row
    # has a matching action_approved audit entry (same suggested_action_id)
    # that already recorded who did it -- so we don't have to leave history
    # blank just because the column didn't exist yet when they approved it.
    op.execute("""
        UPDATE action_log
        SET approved_by_user_id = (
            SELECT actor_user_id FROM contact_audit_log
            WHERE contact_audit_log.suggested_action_id = action_log.suggested_action_id
              AND contact_audit_log.action = 'action_approved'
            ORDER BY contact_audit_log.created_at DESC
            LIMIT 1
        )
        WHERE action_log.suggested_action_id IS NOT NULL
    """)


def downgrade():
    with op.batch_alter_table('action_log', schema=None) as batch_op:
        batch_op.drop_constraint('fk_action_log_approved_by_user', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_action_log_approved_by_user_id'))
        batch_op.drop_column('approved_by_user_id')
