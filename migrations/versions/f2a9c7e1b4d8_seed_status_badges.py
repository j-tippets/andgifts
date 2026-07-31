"""seed status badges (Active/New/Past) and backfill from contact.status

Revision ID: f2a9c7e1b4d8
Revises: d4f7c1a9b3e6
Create Date: 2026-07-31 00:00:00.000000

Contact status (new/active/past) is being surfaced as regular badges
instead of a dedicated status pill, so it can be freely toggled
alongside VIP/Family Friend/etc. This does NOT touch the
contacts.status column or the status-change audit logging -- that
stays in place, untouched and unused by the UI, in case we want to
bring it back. This migration only adds three global badges and
backfills contact_badges so every existing contact starts out with
the badge matching their current status.
"""
from alembic import op
import sqlalchemy as sa
import uuid
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'f2a9c7e1b4d8'
down_revision = 'd4f7c1a9b3e6'
branch_labels = None
depends_on = None

# label -> swatch color, matching the badge-color-swatch palette
STATUS_BADGES = {
    "new": ("New", "#D6E4F5"),
    "active": ("Active", "#FADDA3"),
    "past": ("Past", "#E8E4DA"),
}


def upgrade():
    conn = op.get_bind()
    badges = sa.table(
        "badges",
        sa.column("id", sa.String),
        sa.column("org_id", sa.String),
        sa.column("scope", sa.String),
        sa.column("owner_user_id", sa.String),
        sa.column("label", sa.String),
        sa.column("color", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    contact_badges = sa.table(
        "contact_badges",
        sa.column("contact_id", sa.String),
        sa.column("badge_id", sa.String),
    )
    contacts = sa.table(
        "contacts",
        sa.column("id", sa.String),
        sa.column("status", sa.String),
    )

    badge_ids = {}
    now = datetime.utcnow()
    for status, (label, color) in STATUS_BADGES.items():
        badge_id = str(uuid.uuid4())
        badge_ids[status] = badge_id
        conn.execute(
            badges.insert().values(
                id=badge_id,
                org_id=None,
                scope="global",
                owner_user_id=None,
                label=label,
                color=color,
                created_at=now,
            )
        )

    rows = conn.execute(sa.select(contacts.c.id, contacts.c.status)).fetchall()
    if rows:
        conn.execute(
            contact_badges.insert(),
            [
                {"contact_id": row.id, "badge_id": badge_ids[row.status]}
                for row in rows
                if row.status in badge_ids
            ],
        )


def downgrade():
    conn = op.get_bind()
    badges = sa.table("badges", sa.column("id", sa.String), sa.column("label", sa.String), sa.column("scope", sa.String))
    labels = [label for label, _ in STATUS_BADGES.values()]
    result = conn.execute(
        sa.select(badges.c.id).where(badges.c.scope == "global", badges.c.label.in_(labels))
    ).fetchall()
    ids = [r.id for r in result]
    if ids:
        contact_badges = sa.table("contact_badges", sa.column("badge_id", sa.String))
        conn.execute(sa.delete(contact_badges).where(contact_badges.c.badge_id.in_(ids)))
        conn.execute(sa.delete(badges).where(badges.c.id.in_(ids)))
