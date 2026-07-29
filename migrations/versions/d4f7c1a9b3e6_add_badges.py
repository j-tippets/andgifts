"""add badges and contact_badges tables

Revision ID: d4f7c1a9b3e6
Revises: b1f4a8c2d6e9
Create Date: 2026-07-29 00:00:00.000000

A lightweight on/off tag an agent can attach to a Contact (e.g. "VIP"),
usable as a flow condition the same way interest tags are. Two scopes:
"global" (org_id NULL, platform-admin managed, visible to every org)
and "personal" (owner_user_id set, private to one agent) -- no
org-wide/admin-per-agency tier, unlike CustomFieldDefinition.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4f7c1a9b3e6'
down_revision = 'b1f4a8c2d6e9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "badges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=True),
        sa.Column("scope", sa.Enum("global", "personal", name="badge_scope"), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("badges", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_badges_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_badges_owner_user_id"), ["owner_user_id"], unique=False)

    op.create_table(
        "contact_badges",
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("badge_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["badge_id"], ["badges.id"]),
        sa.PrimaryKeyConstraint("contact_id", "badge_id"),
    )


def downgrade():
    op.drop_table("contact_badges")
    with op.batch_alter_table("badges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_badges_owner_user_id"))
        batch_op.drop_index(batch_op.f("ix_badges_org_id"))
    op.drop_table("badges")
