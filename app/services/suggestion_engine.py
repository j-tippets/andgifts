"""
Suggestion engine: scans upcoming timeline events and generates
SuggestedAction rows for the agent's daily dashboard.

Two generation paths currently coexist:
- generate_suggestions_for_org: the original MVP path, driven by
  GiftTrigger rows (event_type + optional interest -> a single gift).
- generate_campaign_suggestions_for_org: the campaign engine (stage 3),
  driven by active Campaign rows -- richer triggers (signed day offset,
  before/on/after an event), conditions (interest tag, price cap, LLM
  gift selection), and actions beyond just gifts (email/text/
  handwritten_note), optionally with LLM-written copy. See
  app/services/llm.py for the actual API calls and their fallbacks.

Both are safe to run together and are idempotent per (contact, event,
date) -- for campaigns, dedup is additionally scoped per campaign_id so
two different campaigns matching the same event on the same day both
still produce their own suggestion.

Designed to be called either from a nightly cron job (DO App Platform
supports scheduled jobs / worker components) or on-demand from the
dashboard route.
"""
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from app.extensions import db
from app.models import (
    TimelineEvent, SuggestedAction, GiftTrigger, GiftCatalogItem, Contact,
    Campaign, User,
)
from app.services import llm

LOOKAHEAD_DAYS = 14


def generate_suggestions_for_org(org, today=None):
    today = today or date.today()
    window_end = today + timedelta(days=LOOKAHEAD_DAYS)
    available_item_ids = {i.id for i in org.available_catalog_items()}

    events = (
        TimelineEvent.query
        .join(TimelineEvent.contact)
        .filter_by(org_id=org.id)
        .filter(Contact.do_not_contact.is_(False))
        .all()
    )

    created = []
    for event in events:
        occurrence_date = _next_occurrence(event, today, window_end)
        if occurrence_date is None:
            continue

        if _suggestion_exists(org.id, event.contact_id, event.id, occurrence_date):
            continue

        gift_trigger = _match_gift_trigger(org.id, event, available_item_ids)
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


def _match_gift_trigger(org_id, event, available_item_ids):
    """Prefer an org-specific trigger over the global default; prefer a
    trigger matching one of the contact's interests over a generic one.
    Any candidate pointing at a gift the org hasn't curated in (see
    Org.available_catalog_items) is skipped entirely."""
    contact_interest_names = {i.name for i in event.contact.interests}

    candidates = GiftTrigger.query.filter(
        GiftTrigger.event_type == event.event_type,
        (GiftTrigger.org_id == org_id) | (GiftTrigger.org_id.is_(None)),
    ).all()
    candidates = [
        c for c in candidates
        if c.suggested_gift_id is None or c.suggested_gift_id in available_item_ids
    ]
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


# --- Campaign engine (stage 3) -------------------------------------------

def generate_campaign_suggestions_for_org(org, today=None):
    today = today or date.today()
    window_end = today + timedelta(days=LOOKAHEAD_DAYS)
    available_item_ids = {i.id for i in org.available_catalog_items()}

    # Team-wide flows (owner_user_id IS NULL) are shared templates managed
    # by agency admins -- they never fire on their own. An agent has to
    # explicitly "add" one to their profile first, which forks it into
    # their own personal Campaign row (owner_user_id set); only those
    # personal copies are evaluated here.
    campaigns = Campaign.query.filter(
        Campaign.org_id == org.id,
        Campaign.is_active.is_(True),
        Campaign.owner_user_id.isnot(None),
    ).all()

    created = []
    for campaign in campaigns:
        owner = User.query.get(campaign.owner_user_id)
        if owner is None or owner.status != "active":
            continue
        # Personal campaign: applies to any contact visible to that
        # agent -- their own private ones, plus shared org contacts.
        contacts_query = Contact.visible_to(Contact.query.filter_by(org_id=org.id), owner)
        contacts = contacts_query.filter(Contact.do_not_contact.is_(False)).all()

        for contact in contacts:
            if campaign.interest_tag:
                contact_interest_names = {i.name for i in contact.interests}
                if campaign.interest_tag not in contact_interest_names:
                    continue

            matching_events = [e for e in contact.timeline_events if e.event_type == campaign.event_type]
            for event in matching_events:
                trigger_date = _campaign_trigger_date(event, campaign.offset_days, today, window_end)
                if trigger_date is None:
                    continue

                if _campaign_suggestion_exists(org.id, campaign.id, contact.id, event.id, trigger_date):
                    continue

                gift_item, gift_reasoning = None, None
                if campaign.action_type == "gift":
                    gift_item, gift_reasoning = _resolve_campaign_gift(campaign, contact, available_item_ids)

                message = None
                if campaign.action_type in ("email", "text", "handwritten_note"):
                    message = _resolve_campaign_message(campaign, contact, event)

                reason = _build_campaign_reason_text(campaign, contact, event, gift_item, gift_reasoning)

                suggestion = SuggestedAction(
                    org_id=org.id,
                    contact_id=contact.id,
                    triggering_event_id=event.id,
                    source_campaign_id=campaign.id,
                    action_type=campaign.action_type,
                    suggested_gift_id=gift_item.id if gift_item else None,
                    reason_text=reason,
                    generated_message=message,
                    target_date=trigger_date,
                    status="pending",
                )
                db.session.add(suggestion)
                created.append(suggestion)

    if created:
        db.session.commit()
    return created


def _campaign_trigger_date(event, offset_days, today, window_end):
    """The date campaign's action should fire on, or None if that's not
    within [today, window_end]. Handles offsets that push a recurring
    event's occurrence across a year boundary by checking last/this/next
    year's occurrence, not just 'this year'."""
    if not event.is_recurring:
        trigger_date = event.event_date + timedelta(days=offset_days)
        return trigger_date if today <= trigger_date <= window_end else None

    for year_delta in (-1, 0, 1):
        try:
            base = event.event_date.replace(year=today.year + year_delta)
        except ValueError:
            base = event.event_date.replace(year=today.year + year_delta, day=28)  # Feb 29 -> Feb 28
        trigger_date = base + timedelta(days=offset_days)
        if today <= trigger_date <= window_end:
            return trigger_date
    return None


def _campaign_suggestion_exists(org_id, campaign_id, contact_id, event_id, target_date):
    """Scoped per campaign_id (not just contact/event/date) so two
    different campaigns matching the same event on the same day both
    still get their own suggestion -- only the SAME campaign re-running
    is deduplicated."""
    return db.session.query(
        SuggestedAction.query.filter_by(
            org_id=org_id,
            source_campaign_id=campaign_id,
            contact_id=contact_id,
            triggering_event_id=event_id,
            target_date=target_date,
        ).exists()
    ).scalar()


def _resolve_campaign_gift(campaign, contact, available_item_ids):
    if campaign.use_llm_gift_selection:
        candidates = GiftCatalogItem.query.filter(
            GiftCatalogItem.id.in_(available_item_ids), GiftCatalogItem.is_active.is_(True)
        )
        if campaign.price_max_cents:
            candidates = candidates.filter(GiftCatalogItem.price_cents <= campaign.price_max_cents)
        return llm.pick_gift(contact, candidates.all())

    if campaign.suggested_gift_id and campaign.suggested_gift_id in available_item_ids:
        return GiftCatalogItem.query.get(campaign.suggested_gift_id), None

    return None, None


def _resolve_campaign_message(campaign, contact, event):
    if campaign.use_llm_copy:
        return llm.generate_message(campaign.llm_prompt_hint, contact, event)

    template = campaign.message_template or "Hi {contact_name}, following up on your {event_label}."
    return template.format(
        contact_name=contact.household_name,
        event_label=event.display_label(),
        event_date=event.event_date.strftime("%b %-d, %Y"),
    )


def _build_campaign_reason_text(campaign, contact, event, gift_item, gift_reasoning):
    base = (
        f"{campaign.name}: {contact.household_name}'s {event.display_label()} "
        f"({event.event_date.strftime('%b %-d, %Y')})."
    )
    if gift_item:
        base += f" Suggested gift: {gift_item.name}."
        if gift_reasoning:
            base += f" {gift_reasoning}"
    return base
