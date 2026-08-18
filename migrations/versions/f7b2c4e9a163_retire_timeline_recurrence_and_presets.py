"""retire timeline recurrence and hardcoded date milestones

Revision ID: f7b2c4e9a163
Revises: d3e8a1c9f204
Create Date: 2026-08-18 00:00:00.000000

Two cleanups now that Important Dates is its own UI element and owns
recurring dates end to end:

1. "Repeats annually" is retired for ordinary Timeline milestones --
   clears is_recurring/recurrence_rule back to false/"none" on every
   TimelineEvent that ISN'T an Important Date. Important Date rows
   (is_important_date=True) are untouched -- they're always annual by
   definition, set in app/routes/contacts.py's _apply_timeline_form.

2. Removes the hardcoded birthday/anniversary-style presets from the
   Timeline's "Event type" dropdown: birthday, six_month_anniversary,
   one_year_anniversary, wedding_anniversary. Deletes both the
   PracticeTypeMilestone template rows (so newly-created orgs stop
   getting them seeded) and the already-seeded CustomEventType rows
   for every existing org (so they stop showing up as Timeline
   dropdown choices right away). first_contact/showing/offer_made/
   closing are untouched.

   Note for whoever's running this against production: any live Flow
   already targeting one of these event_type keys (e.g. a
   birthday-triggered gift flow) keeps running -- CampaignRecipe/
   Campaign.event_type is a plain string column, not an FK to
   CustomEventType, so nothing breaks functionally. The one visible
   effect is the flow-builder's Event type dropdown won't have a
   matching label for it anymore if that flow is ever re-edited.
   TimelineEvent/CustomEventType have no FK relationship either
   (see app/models/timeline.py), so existing TimelineEvent rows that
   already used one of these keys keep displaying fine via their own
   stored label -- they just won't be offered as a NEW dropdown choice.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f7b2c4e9a163'
down_revision = 'd3e8a1c9f204'
branch_labels = None
depends_on = None

RETIRED_DATE_MILESTONE_KEYS = (
    "birthday", "six_month_anniversary", "one_year_anniversary", "wedding_anniversary",
)


def upgrade():
    conn = op.get_bind()

    timeline_events = sa.table(
        "timeline_events",
        sa.column("is_important_date", sa.Boolean),
        sa.column("is_recurring", sa.Boolean),
        sa.column("recurrence_rule", sa.Enum("annual", "none", name="recurrence_rule")),
    )
    conn.execute(
        timeline_events.update()
        .where(timeline_events.c.is_important_date.is_(False))
        .values(is_recurring=False, recurrence_rule="none")
    )

    practice_type_milestones = sa.table(
        "practice_type_milestones", sa.column("key", sa.String),
    )
    conn.execute(
        practice_type_milestones.delete().where(
            practice_type_milestones.c.key.in_(RETIRED_DATE_MILESTONE_KEYS)
        )
    )

    custom_event_types = sa.table(
        "custom_event_types", sa.column("key", sa.String),
    )
    conn.execute(
        custom_event_types.delete().where(
            custom_event_types.c.key.in_(RETIRED_DATE_MILESTONE_KEYS)
        )
    )


def downgrade():
    # Deliberately no-op: the deleted PracticeTypeMilestone/
    # CustomEventType rows and the cleared is_recurring flags aren't
    # recoverable from data alone (we don't know which Timeline rows
    # were recurring before this ran, or the original preset sort
    # order), same as every other data-cleanup migration in this repo.
    pass
