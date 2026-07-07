"""
Suggestion engine: scans upcoming timeline events and generates
SuggestedAction rows for the agent's daily dashboard.

MVP version is pure rule-based (date math + tag matching) with a
template-generated reason string. The LLM-written "why" blurb and
smarter gift ranking is a drop-in upgrade later -- swap
`_build_reason_text` to call the Anthropic API instead of formatting
a template, without touching the scheduling logic below.

Designed to be called either from a nightly cron job (DO App Platform
supports scheduled jobs / worker components) or on-demand from the
dashboard route, since it's idempotent per (contact, event, date).
"""
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from app.extensions import db
from app.models import TimelineEvent, SuggestedAction, GiftTrigger, GiftCatalogItem

LOOKAHEAD_DAYS = 14


def generate_suggestions_for_org(org, today=None):
    today = today or date.today()
    window_end = today + timedelta(days=LOOKAHEAD_DAYS)

    events = (
        TimelineEvent.query
        .join(TimelineEvent.contact)
        .filter_by(org_id=org.id)
        .all()
    )

    created = []
    for event in events:
        occurrence_date = _next_occurrence(event, today, window_end)
        if occurrence_date is None:
            continue

        if _suggestion_exists(org.id, event.contact_id, event.id, occurrence_date):
            continue

        gift_trigger = _match_gift_trigger(org.id, event)
        reason = _build_reason_text(event, occurrence_date, gift_trigger)

        suggestion = SuggestedAction(
            org_id=org.id,
            contact_id=event.contact_id,
            triggering_event_id=event.id,
            action_type="gift" if gift_trigger else "email",
            suggested_gift_id=gift_trigger.suggested_gift_id if gift_trigger else None,
            reason_text=reason,
            target_date=occurrence_date,
            status="pending",
        )
        db.session.add(suggestion)
        created.append(suggestion)

    if created:
        db.session.commit()
    return created


def _next_occurrence(event, today, window_end):
    """Returns the event's next relevant date within the window, or None."""
    if not event.is_recurring:
        # One-time events only surface if they're upcoming (rare -- usually
        # these are logged in the past) or exactly today.
        return event.event_date if today <= event.event_date <= window_end else None

    # Recurring (annual): find this year's (or next year's) occurrence
    candidate = event.event_date.replace(year=today.year)
    if candidate < today:
        candidate = candidate.replace(year=today.year + 1)
    return candidate if today <= candidate <= window_end else None


def _suggestion_exists(org_id, contact_id, event_id, target_date):
    return db.session.query(
        SuggestedAction.query.filter_by(
            org_id=org_id,
            contact_id=contact_id,
            triggering_event_id=event_id,
            target_date=target_date,
        ).exists()
    ).scalar()


def _match_gift_trigger(org_id, event):
    """Prefer an org-specific trigger over the global default; prefer a
    trigger matching one of the contact's interests over a generic one."""
    contact_interest_names = {i.name for i in event.contact.interests}

    candidates = GiftTrigger.query.filter(
        GiftTrigger.event_type == event.event_type,
        (GiftTrigger.org_id == org_id) | (GiftTrigger.org_id.is_(None)),
    ).all()
    if not candidates:
        return None

    # Interest-matched, org-specific first
    for c in candidates:
        if c.org_id == org_id and c.interest_tag in contact_interest_names:
            return c
    # Interest-matched, global
    for c in candidates:
        if c.org_id is None and c.interest_tag in contact_interest_names:
            return c
    # Org-specific, no interest requirement
    for c in candidates:
        if c.org_id == org_id and not c.interest_tag:
            return c
    # Global fallback
    for c in candidates:
        if c.org_id is None and not c.interest_tag:
            return c
    return None


def _build_reason_text(event, occurrence_date, gift_trigger):
    contact_name = event.contact.household_name
    label = event.display_label()
    days_out = (occurrence_date - date.today()).days
    when = "today" if days_out == 0 else f"in {days_out} days"

    base = f"{contact_name}'s {label} is coming up {when} ({occurrence_date.strftime('%b %-d')})."
    if gift_trigger and gift_trigger.suggested_gift:
        base += f" Suggested gift: {gift_trigger.suggested_gift.name}."
    return base
