"""add practice types and preset milestones

Revision ID: c8a4f102d6e9
Revises: 3e91c6b4a207
Create Date: 2026-08-10 00:00:00.000000

Removes the hardcoded STANDARD_EVENT_TYPES list in favor of
admin-managed PracticeType/PracticeTypeMilestone presets (see
app/models/practice_types.py). This migration:
  1. creates practice_types and practice_type_milestones
  2. adds orgs.practice_type_id
  3. seeds one 'Real Estate' PracticeType with the 8 milestones that
     used to be hardcoded (everything from the old list except
     'custom', which stays a code-level sentinel -- see
     CUSTOM_MILESTONE_KEY in app/models/timeline.py)
  4. assigns every existing org to that Real Estate practice type
  5. backfills a real, per-org CustomEventType row for each of those 8
     milestones for every existing org, so they become ordinary,
     rename/removable milestones instead of a hardcoded second tier --
     matching what services.practice_types.seed_org_milestones does
     for orgs created after this point
"""
from alembic import op
import sqlalchemy as sa
import uuid
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'c8a4f102d6e9'
down_revision = '3e91c6b4a207'
branch_labels = None
depends_on = None

# (key, label) in the same order the old STANDARD_EVENT_TYPES list had
# them, minus 'custom'.
REAL_ESTATE_MILESTONES = [
    ("first_contact", "First Contact"),
    ("showing", "Showing"),
    ("offer_made", "Offer Made"),
    ("closing", "Closing"),
    ("six_month_anniversary", "Six Month Anniversary"),
    ("one_year_anniversary", "One Year Anniversary"),
    ("wedding_anniversary", "Wedding Anniversary"),
    ("birthday", "Birthday"),
]


def upgrade():
    op.create_table(
        'practice_types',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_table(
        'practice_type_milestones',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('practice_type_id', sa.String(length=36), nullable=False),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['practice_type_id'], ['practice_types.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('practice_type_id', 'key', name='uq_practice_type_milestone_key'),
    )
    with op.batch_alter_table('practice_type_milestones', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_practice_type_milestones_practice_type_id'),
            ['practice_type_id'], unique=False,
        )

    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('practice_type_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_orgs_practice_type_id', 'practice_types', ['practice_type_id'], ['id']
        )

    # --- data backfill ---------------------------------------------
    conn = op.get_bind()
    now = datetime.utcnow()

    practice_types = sa.table(
        'practice_types',
        sa.column('id', sa.String), sa.column('key', sa.String),
        sa.column('name', sa.String), sa.column('created_at', sa.DateTime),
    )
    practice_type_milestones = sa.table(
        'practice_type_milestones',
        sa.column('id', sa.String), sa.column('practice_type_id', sa.String),
        sa.column('key', sa.String), sa.column('label', sa.String),
        sa.column('sort_order', sa.Integer), sa.column('created_at', sa.DateTime),
    )
    orgs = sa.table('orgs', sa.column('id', sa.String), sa.column('practice_type_id', sa.String))
    custom_event_types = sa.table(
        'custom_event_types',
        sa.column('id', sa.String), sa.column('org_id', sa.String),
        sa.column('scope', sa.String), sa.column('owner_user_id', sa.String),
        sa.column('key', sa.String), sa.column('label', sa.String),
        sa.column('created_at', sa.DateTime),
    )

    real_estate_id = str(uuid.uuid4())
    conn.execute(practice_types.insert().values(
        id=real_estate_id, key='real_estate', name='Real Estate', created_at=now,
    ))
    conn.execute(practice_type_milestones.insert(), [
        {
            'id': str(uuid.uuid4()), 'practice_type_id': real_estate_id,
            'key': key, 'label': label, 'sort_order': i, 'created_at': now,
        }
        for i, (key, label) in enumerate(REAL_ESTATE_MILESTONES)
    ])

    org_rows = conn.execute(sa.select(orgs.c.id)).fetchall()
    if org_rows:
        conn.execute(
            orgs.update().values(practice_type_id=real_estate_id)
        )

        # Only backfill a (org, key) pair that doesn't already exist --
        # an org could theoretically already have a personal/org
        # milestone using one of these keys (blocked going forward by
        # the collision check in routes/contacts.new_event_type, but
        # this migration shouldn't assume that held true historically).
        existing = conn.execute(
            sa.select(custom_event_types.c.org_id, custom_event_types.c.key)
        ).fetchall()
        existing_pairs = {(row.org_id, row.key) for row in existing}

        rows_to_insert = [
            {
                'id': str(uuid.uuid4()), 'org_id': org_row.id, 'scope': 'org',
                'owner_user_id': None, 'key': key, 'label': label, 'created_at': now,
            }
            for org_row in org_rows
            for key, label in REAL_ESTATE_MILESTONES
            if (org_row.id, key) not in existing_pairs
        ]
        if rows_to_insert:
            conn.execute(custom_event_types.insert(), rows_to_insert)


def downgrade():
    conn = op.get_bind()
    practice_types = sa.table('practice_types', sa.column('id', sa.String), sa.column('key', sa.String))
    custom_event_types = sa.table(
        'custom_event_types', sa.column('org_id', sa.String), sa.column('key', sa.String),
    )
    real_estate_keys = [key for key, _ in REAL_ESTATE_MILESTONES]
    conn.execute(
        sa.delete(custom_event_types).where(custom_event_types.c.key.in_(real_estate_keys))
    )

    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_orgs_practice_type_id', type_='foreignkey')
        batch_op.drop_column('practice_type_id')

    with op.batch_alter_table('practice_type_milestones', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_practice_type_milestones_practice_type_id'))
    op.drop_table('practice_type_milestones')
    op.drop_table('practice_types')
