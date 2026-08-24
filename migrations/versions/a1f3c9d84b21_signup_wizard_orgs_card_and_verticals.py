"""signup wizard: org subscription card + new practice type verticals

Revision ID: a1f3c9d84b21
Revises: 230208531e85
Create Date: 2026-08-24 00:00:00.000000

Supports the new multi-step signup wizard (see routes/onboarding.py):
  1. adds orgs.stripe_default_payment_method_id + card display columns
     -- the Team tier's "card on file" captured at signup, separate
     from Org.stripe_customer_id/stripe_subscription_id (paid-tier
     self-serve checkout) and from PaymentMethod (per-agent gift cards)
  2. seeds four new PracticeType verticals the wizard's company-type
     step offers alongside the existing Real Estate: HR Manager,
     Executive Assistant, Loan Officer, Insurance Agent -- each with a
     starter milestone preset, same pattern as c8a4f102d6e9's Real
     Estate seed
  3. seeds an 'Other' PracticeType with NO milestones on purpose --
     orgs that pick it start with an empty milestone list and build
     their own from scratch (see PracticeType docstring)
"""
from alembic import op
import sqlalchemy as sa
import uuid
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'a1f3c9d84b21'
down_revision = '230208531e85'
branch_labels = None
depends_on = None

VERTICALS = {
    "hr_manager": (
        "HR Manager",
        [
            ("new_hire_start", "New Hire Start Date"),
            ("ninety_day_milestone", "90-Day Milestone"),
            ("work_anniversary", "Work Anniversary"),
            ("promotion", "Promotion"),
            ("birthday", "Birthday"),
        ],
    ),
    "executive_assistant": (
        "Executive Assistant",
        [
            ("onboarding_complete", "Onboarding Complete"),
            ("boss_work_anniversary", "Boss's Work Anniversary"),
            ("team_milestone", "Team Milestone"),
            ("holiday_season", "Holiday Season"),
            ("birthday", "Birthday"),
        ],
    ),
    "loan_officer": (
        "Loan Officer",
        [
            ("application_submitted", "Application Submitted"),
            ("loan_approved", "Loan Approved"),
            ("closing", "Closing"),
            ("one_year_anniversary", "One Year Anniversary"),
            ("birthday", "Birthday"),
        ],
    ),
    "insurance_agent": (
        "Insurance Agent",
        [
            ("policy_bound", "Policy Bound"),
            ("policy_renewal", "Policy Renewal"),
            ("claim_resolved", "Claim Resolved"),
            ("one_year_anniversary", "One Year Anniversary"),
            ("birthday", "Birthday"),
        ],
    ),
    # No milestone list -- orgs that pick "Other" start empty.
    "other": ("Other", []),
}


def upgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stripe_default_payment_method_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('card_brand', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('card_last4', sa.String(length=4), nullable=True))
        batch_op.add_column(sa.Column('card_exp_month', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('card_exp_year', sa.Integer(), nullable=True))

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

    for key, (name, milestones) in VERTICALS.items():
        existing = conn.execute(
            sa.select(practice_types.c.id).where(practice_types.c.key == key)
        ).first()
        if existing:
            continue  # already present (e.g. re-run/hand-seeded) -- don't duplicate

        pt_id = str(uuid.uuid4())
        conn.execute(practice_types.insert().values(
            id=pt_id, key=key, name=name, created_at=now,
        ))
        if milestones:
            conn.execute(practice_type_milestones.insert(), [
                {
                    'id': str(uuid.uuid4()), 'practice_type_id': pt_id,
                    'key': m_key, 'label': label, 'sort_order': i, 'created_at': now,
                }
                for i, (m_key, label) in enumerate(milestones)
            ])


def downgrade():
    conn = op.get_bind()
    practice_types = sa.table('practice_types', sa.column('id', sa.String), sa.column('key', sa.String))
    practice_type_milestones = sa.table(
        'practice_type_milestones', sa.column('practice_type_id', sa.String),
    )

    keys = list(VERTICALS.keys())
    rows = conn.execute(
        sa.select(practice_types.c.id).where(practice_types.c.key.in_(keys))
    ).fetchall()
    ids = [r.id for r in rows]
    if ids:
        conn.execute(
            sa.delete(practice_type_milestones).where(practice_type_milestones.c.practice_type_id.in_(ids))
        )
        conn.execute(sa.delete(practice_types).where(practice_types.c.id.in_(ids)))

    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_column('card_exp_year')
        batch_op.drop_column('card_exp_month')
        batch_op.drop_column('card_last4')
        batch_op.drop_column('card_brand')
        batch_op.drop_column('stripe_default_payment_method_id')
