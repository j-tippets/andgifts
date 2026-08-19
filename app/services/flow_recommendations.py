"""
Flow recommendations: "you might want a flow for this" cards on the
Today dashboard -- distinct from SuggestedAction ("reach out to this
contact"). See app/models/actions.py:FlowRecommendation for the model
and its docstring for the one-row-per-(user, event_type) invariant.

Detection strategy (deliberately the simplest legible signal, not an
open-ended "AI reviews your whole setup" -- see the chat discussion
this came out of): a milestone type is a "coverage gap" for an agent
if several of their own visible contacts have it logged on their
timeline, but none of that agent's own active Campaigns has that
event_type as its trigger. Not date-windowed -- a coverage gap is a
coverage gap whether or not any contact's occurrence is imminent right
now, and checking the full history instead of just the 14-day
suggestion lookahead window means this doesn't flicker in and out of
existence day to day.

Deliberately does NOT try to guess whether the agent should reach out
proactively with no event backing it -- see the chat discussion this
came from: the trigger stays event-driven and legible (a milestone a
contact genuinely has), and the AI's actual value-add stays downstream
of that (what to send, which is what generate_campaign_suggestions_for_org
already does once a flow exists) rather than inventing the "should I
reach out at all" judgment call from nothing.
"""
from datetime import datetime

from app.extensions import db
from app.models import (
    Contact, TimelineEvent, Campaign, CustomEventType, FlowRecommendation,
    CUSTOM_MILESTONE_KEY,
)

# Below this many contacts sharing a milestone, it's not worth
# interrupting the agent to suggest automating it -- a one-off doesn't
# need a flow.
MIN_CONTACTS_FOR_RECOMMENDATION = 3


def generate_flow_recommendations_for_user(user, today=None):
    """Scans this agent's own visible contacts for milestone types with
    no active personal flow covering them, and files a new pending
    FlowRecommendation for each gap found. Safe to call on every Today
    load -- see FlowRecommendation's docstring for why it never
    duplicates or resurfaces a row once one exists for a given
    (user, event_type), so repeated calls only ever add genuinely new
    gaps, never re-flag ones the agent already accepted or dismissed."""
    contacts = Contact.visible_to(Contact.query.filter_by(org_id=user.org_id), user).all()
    contact_ids = [c.id for c in contacts]
    if not contact_ids:
        return []

    # Distinct (event_type, contact_id) pairs across this agent's whole
    # book, excluding the freeform "custom" one-off milestone type --
    # that key covers many unrelated agent-typed labels (see
    # CUSTOM_MILESTONE_KEY's docstring in app/models/timeline.py), so
    # it isn't a single coherent thing to build one flow around.
    rows = (
        db.session.query(TimelineEvent.event_type, TimelineEvent.contact_id)
        .filter(TimelineEvent.contact_id.in_(contact_ids))
        .filter(TimelineEvent.event_type != CUSTOM_MILESTONE_KEY)
        .distinct()
        .all()
    )
    contacts_by_type = {}
    for event_type, contact_id in rows:
        contacts_by_type.setdefault(event_type, set()).add(contact_id)

    covered_types = {
        c.event_type
        for c in Campaign.query.filter_by(owner_user_id=user.id, is_active=True).all()
    }

    labels = {
        et.key: et.label
        for et in CustomEventType.visible_to(
            CustomEventType.query.filter_by(org_id=user.org_id), user
        ).all()
    }

    already_flagged = {
        r.event_type
        for r in FlowRecommendation.query.filter_by(user_id=user.id).all()
    }

    created = []
    for event_type, contact_id_set in contacts_by_type.items():
        if event_type in covered_types or event_type in already_flagged:
            continue
        if len(contact_id_set) < MIN_CONTACTS_FOR_RECOMMENDATION:
            continue

        rec = FlowRecommendation(
            org_id=user.org_id,
            user_id=user.id,
            event_type=event_type,
            event_label=labels.get(event_type, event_type.replace("_", " ").title()),
            contact_count=len(contact_id_set),
            status="pending",
        )
        db.session.add(rec)
        created.append(rec)

    if created:
        db.session.commit()
    return created
