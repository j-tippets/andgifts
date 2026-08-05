"""replace per-agent sender identity with org-level sender local-part

Revision ID: c7d2f4a91e60
Revises: b4e19a6cf203
Create Date: 2026-08-06 00:00:00.000000

Per-agent SendGrid Single Sender Verification (added in b4e19a6cf203)
turned out to require the SendGrid *account owner* to log in and click
a verify button for every single agent -- it doesn't scale past a
handful of agents. Replaced with one shared, domain-authenticated From
address per org (Org.sender_local_part) plus the agent's own email as
Reply-To, which needs no per-agent verification step at all.
"""
import re
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column

# revision identifiers, used by Alembic.
revision = 'c7d2f4a91e60'
down_revision = 'b4e19a6cf203'
branch_labels = None
depends_on = None


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return slug or "agency"


def upgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sender_local_part', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_orgs_sender_local_part', ['sender_local_part'])

    # Backfill: every existing org gets a slugified local-part, with a
    # numeric suffix on collision -- mirrors Org.generate_sender_local_part
    # so behavior matches what new orgs get going forward.
    conn = op.get_bind()
    orgs_t = table('orgs', column('id', sa.String), column('name', sa.String), column('sender_local_part', sa.String))
    existing_slugs = set()
    for org_id, name in conn.execute(sa.text("SELECT id, name FROM orgs")):
        base = _slugify(name)
        candidate = base
        suffix = 1
        while candidate in existing_slugs:
            suffix += 1
            candidate = f"{base}{suffix}"
        existing_slugs.add(candidate)
        conn.execute(
            orgs_t.update().where(orgs_t.c.id == org_id).values(sender_local_part=candidate)
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('sender_verified')
        batch_op.drop_column('sendgrid_sender_id')
        batch_op.drop_column('sender_name')
        batch_op.drop_column('sender_email')


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sender_email', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('sender_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('sendgrid_sender_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            'sender_verified', sa.Boolean(), nullable=False, server_default=sa.false()
        ))

    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_constraint('uq_orgs_sender_local_part', type_='unique')
        batch_op.drop_column('sender_local_part')
