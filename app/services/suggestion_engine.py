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
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta

from app.extensions import db
from app.models import (
    TimelineEvent, SuggestedAction, GiftTrigger, GiftCatalogItem, Contact,
    Campaign, User, ContactAuditLog, EXPIRATION_GRACE_DAYS,
)
from app.services import llm
from app.services import campaign_rules

LOOKAHEAD_DAYS = 14


def _log_qualified(suggestion, contact):
    """Records that a contact newly qualified for a suggested action --
    fired the moment a SuggestedAction row is created, from either
    generation path. Not tied to a user (this runs unattended, e.g. from
    the on-demand dashboard generation or a future cron job), so it's
    attributed to "System" the same way Stripe-driven ActionLog entries
    are attributed to "Stripe checkout" rather than a real user.
    suggestion.id must already be flushed to the DB before this is
    called, since it's stored as a FK on the log row."""
    kind = suggestion.action_type.replace("_", " ")
    if suggestion.action_type == "gift" and suggestion.suggested_gift_id:
        gift = GiftCatalogItem.query.get(suggestion.suggested_gift_id)
        summary = (
            f"Qualified for a suggested gift \u2014 {gift.name} \u2014 for {contact.household_name}."
            if gift else f"Qualified for a suggested gift for {contact.household_name}."
        )
    else:
        summary = f"Qualified for a suggested {kind} for {contact.household_name}."

    db.session.add(ContactAuditLog(
        org_id=suggestion.org_id,
        contact_id=contact.id,
        contact_name_snapshot=contact.household_name,
        actor_user_id=None,
        actor_name_snapshot="System",
        action="action_suggested",
        summary=summary,
        suggested_action_id=suggestion.id,
    ))


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

        note = None
        if gift_trigger and gift_trigger.suggested_gift:
            note = llm.generate_gift_note(event.contact, event, gift_trigger.suggested_gift)

        suggestion = SuggestedAction(
            org_id=org.id,
            contact_id=event.contact_id,
            triggering_event_id=event.id,
            source_campaign_id=None,
            action_type="gift" if gift_trigger else "email",
            suggested_gift_id=gift_trigger.suggested_gift_id if gift_trigger else None,
            reason_text=reason,
            generated_message=note,
            target_date=occurrence_date,
            status="pending",
        )
        db.session.add(suggestion)
        db.session.flush()  # populate suggestion.id before logging the FK reference
        _log_qualified(suggestion, event.contact)
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
    """True if a SuggestedAction already exists for this exact (contact,
    event, target_date) tuple, regardless of its current status (pending,
    approved, skipped, or deleted). This is what stops a deleted or
    skipped suggestion from immediately regenerating -- but since it's
    scoped to target_date, it does NOT block a recurring event's next
    occurrence (a different date) from qualifying again next year. A
    deleted purchase-anniversary gift, for example, still lets the
    contact qualify for next year's anniversary."""
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

    # Every live Campaign belongs to one agent (owner_user_id is always
    # set) -- agency-wide automation is now authored as a local flow in
    # the Flow Library instead (see CampaignRecipe.org_id), which each
    # agent copies into their own personal Campaign. The isnot(None)
    # filter here is just a defensive guard against any legacy rows.
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

                if not campaign_rules.evaluate_rules(campaign, contact, event, org, today):
                    continue

                gift_item, gift_reasoning = None, None
                if campaign.action_type == "gift":
                    gift_item, gift_reasoning = _resolve_campaign_gift(campaign, contact, available_item_ids)

                message = None
                if campaign.action_type in ("email", "text", "handwritten_note"):
                    message = _resolve_campaign_message(campaign, contact, event)
                elif campaign.action_type == "gift" and gift_item:
                    message = llm.generate_gift_note(contact, event, gift_item)

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
                db.session.flush()  # populate suggestion.id before logging the FK reference
                _log_qualified(suggestion, contact)
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
    for the SAME date is deduplicated. Scoping to target_date also means
    a deleted or skipped suggestion doesn't block this campaign's next
    occurrence of a recurring event (a different date) from qualifying
    again later."""
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
    if campaign_rules.uses_llm_gift_selection(campaign):
        candidates = GiftCatalogItem.query.filter(
            GiftCatalogItem.id.in_(available_item_ids), GiftCatalogItem.is_active.is_(True)
        )
        price_cap = campaign_rules.get_price_cap_cents(campaign)
        if price_cap:
            candidates = candidates.filter(GiftCatalogItem.price_cents <= price_cap)
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


# --- Flow preview (dry run, no side effects) -----------------------------

def preview_flow_matches(spec, contacts, org, today=None, limit=20):
    """Dry-run a flow's trigger/condition/action logic against a list of
    contacts, WITHOUT creating or persisting any SuggestedAction rows.
    Powers the 'Preview' button on the flow builder so agents and agency
    admins can see what a flow would actually produce before it goes
    live -- same matching and gift/message resolution the real engine
    uses, just never written to the database.

    `spec` only needs to duck-type the same fields Campaign and
    CampaignRecipe both already have: name, event_type, offset_days,
    interest_tag, price_max_cents, use_llm_gift_selection, action_type,
    suggested_gift_id, use_llm_copy, message_template, llm_prompt_hint.

    This makes REAL LLM calls when use_llm_gift_selection/use_llm_copy
    are set -- it's a genuine dry run of what would be generated, not a
    mock, so it costs the same as a real suggestion would.

    Does not check for already-existing suggestions (there's nothing to
    dedupe against for a flow that isn't live yet) -- it shows every
    match in the lookahead window, capped at `limit` results.
    """
    today = today or date.today()
    window_end = today + timedelta(days=LOOKAHEAD_DAYS)
    available_item_ids = {i.id for i in org.available_catalog_items()}

    results = []
    for contact in contacts:
        if len(results) >= limit:
            break
        if contact.do_not_contact:
            continue
        if spec.interest_tag:
            contact_interest_names = {i.name for i in contact.interests}
            if spec.interest_tag not in contact_interest_names:
                continue

        matching_events = [e for e in contact.timeline_events if e.event_type == spec.event_type]
        for event in matching_events:
            if len(results) >= limit:
                break
            trigger_date = _campaign_trigger_date(event, spec.offset_days, today, window_end)
            if trigger_date is None:
                continue

            gift_item, gift_reasoning = None, None
            if spec.action_type == "gift":
                gift_item, gift_reasoning = _resolve_campaign_gift(spec, contact, available_item_ids)

            message = None
            if spec.action_type in ("email", "text", "handwritten_note"):
                message = _resolve_campaign_message(spec, contact, event)
            elif spec.action_type == "gift" and gift_item:
                message = llm.generate_gift_note(contact, event, gift_item)

            results.append({
                "contact_name": contact.household_name,
                "event_label": event.display_label(),
                "event_date": event.event_date,
                "trigger_date": trigger_date,
                "gift_name": gift_item.name if gift_item else None,
                "gift_price_cents": gift_item.price_cents if gift_item else None,
                "gift_reasoning": gift_reasoning,
                "message": message,
            })

    return results


def _log_expired(action, contact):
    """Records that a pending suggestion aged out unactioned -- fired from
    expire_stale_suggestions. Attributed to "System" the same way
    _log_qualified is, since this runs unattended from the nightly job."""
    kind = action.action_type.replace("_", " ")
    if action.action_type == "gift" and action.suggested_gift_id:
        gift = GiftCatalogItem.query.get(action.suggested_gift_id)
        summary = (
            f"Suggested gift \u2014 {gift.name} \u2014 for {contact.household_name} expired after "
            f"{EXPIRATION_GRACE_DAYS} days with no action taken."
            if gift else
            f"Suggested gift for {contact.household_name} expired after "
            f"{EXPIRATION_GRACE_DAYS} days with no action taken."
        )
    else:
        summary = (
            f"Suggested {kind} for {contact.household_name} expired after "
            f"{EXPIRATION_GRACE_DAYS} days with no action taken."
        )

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=contact.id,
        contact_name_snapshot=contact.household_name,
        actor_user_id=None,
        actor_name_snapshot="System",
        action="action_expired",
        summary=summary,
        suggested_action_id=action.id,
    ))


def expire_stale_suggestions(org, today=None):
    """
    Auto-expires pending suggestions once they're EXPIRATION_GRACE_DAYS past
    their target_date -- an unactioned "happy anniversary" gift suggestion
    sitting pending three weeks after the anniversary passed is worse than
    useless, and this keeps the dashboard limited to things still worth
    acting on. Based on target_date, not created_at, so a suggestion that
    was itself generated late doesn't get a fresh grace window.

    Deliberately a distinct status from "skipped" (an agent's deliberate
    choice) -- this is the system giving up, not the agent, and the contact
    audit log entry says so. Doesn't affect recurrence: dedup in
    _suggestion_exists / _campaign_suggestion_exists is scoped to
    (contact, event, target_date), so an expired suggestion here still
    lets a recurring event qualify again next year.

    Returns the list of SuggestedAction rows that were expired, for the
    nightly job's log output.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=EXPIRATION_GRACE_DAYS)

    stale = (
        SuggestedAction.query
        .filter(
            SuggestedAction.org_id == org.id,
            SuggestedAction.status == "pending",
            SuggestedAction.target_date < cutoff,
        )
        .all()
    )

    for action in stale:
        action.status = "expired"
        action.resolved_at = datetime.utcnow()
        _log_expired(action, action.contact)

    db.session.commit()
    return stale
