"""add contact_import_jobs

Revision ID: d5b21c9a4e08
Revises: c8a3f61d0e57
Create Date: 2026-09-09 00:00:00.000000

Holds an uploaded CSV between the preview and confirm steps of the
contact import. See models/contact_import for why this is a table
rather than the session (too small), Spaces (public URLs, and this is
client PII) or the container filesystem (ephemeral).

Rows are short-lived: deleted on commit or cancel, and swept after 24
hours by the existing reconcile job for anyone who walks away
mid-review.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd5b21c9a4e08'
down_revision = 'c8a3f61d0e57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'contact_import_jobs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=True),
        # MEDIUMTEXT on MySQL. The route caps uploads well below this;
        # the headroom exists so a large legitimate export is never
        # truncated into a silently partial import.
        sa.Column('csv_text', sa.Text(length=16777215), nullable=False),
        sa.Column('assign_to_uploader', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contact_import_jobs_org_id', 'contact_import_jobs', ['org_id'])
    op.create_index('ix_contact_import_jobs_created_by_user_id', 'contact_import_jobs', ['created_by_user_id'])
    op.create_index('ix_contact_import_jobs_created_at', 'contact_import_jobs', ['created_at'])


def downgrade():
    op.drop_index('ix_contact_import_jobs_created_at', table_name='contact_import_jobs')
    op.drop_index('ix_contact_import_jobs_created_by_user_id', table_name='contact_import_jobs')
    op.drop_index('ix_contact_import_jobs_org_id', table_name='contact_import_jobs')
    op.drop_table('contact_import_jobs')
