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
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    SuggestedAction, GiftTrigger, GiftCatalogItem, Contact,
    Campaign, User, ContactAuditLog, EXPIRATION_GRACE_DAYS,
)
from app.services import llm
from app.services import campaign_rules

LOOKAHEAD_DAYS = 14

# --- Cross-event priority --------------------------------------------
#
# When a contact has more than one timeline event qualifying in the same
# window (e.g. the first_contact event auto-seeded on creation, plus a
# closing date backfilled the same day), only the single most significant
# one should ever generate a suggestion -- see _winning_events_for_contact,
# used by both generation paths below.
#
# Priority is entirely agent-owned (see MilestonePriority) -- there's no
# built-in ranking baked into the app anymore. Whichever agent owns the
# contact (Contact.owner_user_id) is whose ranking applies; an unassigned
# contact has no owner to consult, so everything ties at the default
# tier and ties break on date/creation order alone.
def _event_type_priority(owner, event_type):
    from app.models import MilestonePriority
    return MilestonePriority.priority_for(owner, event_type)


def _event_type_has_active_demand(event_type, org):
    """True if this event_type has at least one real, live thing that
    could act on it -- an org-visible GiftTrigger (legacy path) or an
    active Campaign of any agent's in this org (campaign/flow engine).
    Used to keep an event with NO flow behind it (most commonly the
    auto-seeded First Contact event, which nothing targets by default)
    out of the same-day priority contest entirely, so it can never
    block a real milestone (Showing, Referral Given, Closing, ...)
    that genuinely does have a flow. An event type with no demand at
    all simply never produces a suggestion anyway -- see
    generate_suggestions_for_org's gift_trigger is None branch and
    generate_campaign_suggestions_for_org's matching_events check --
    so excluding it here changes nothing about whether IT gets a
    suggestion, only whether it can outrank something that would.
    """
    has_gift_trigger = db.session.query(
        GiftTrigger.query.filter(
            GiftTrigger.event_type == event_type,
            (GiftTrigger.org_id == org.id) | (GiftTrigger.org_id.is_(None)),
        ).exists()
    ).scalar()
    if has_gift_trigger:
        return True
    return db.session.query(
        Campaign.query.filter(
            Campaign.org_id == org.id,
            Campaign.is_active.is_(True),
            Campaign.owner_user_id.isnot(None),
            Campaign.event_type == event_type,
        ).exists()
    ).scalar()


def _winning_events_for_contact(contact, today, window_end, org):
    """Among this contact's timeline events with a qualifying occurrence
    in [today, window_end] (see _next_occurrence) AND at least one real
    flow behind their event_type (see _event_type_has_active_demand),
    groups them by occurrence DATE and returns the single highest-
    priority event within each date-group (per the contact owner's own
    ranking -- see _event_type_priority). Returns a dict
    {occurrence_date: event}.

    This is the one-suggestion-per-contact-per-day design
    MilestonePriority exists for: if a contact has a Showing, a
    Referral Given, AND a Closing all landing on the same date, only
    the single highest-ranked one should generate a suggestion that
    day, not all three. The demand filter is what makes this safe --
    without it, the auto-seeded First Contact event (dated the day the
    contact is created, almost always the same day a real milestone
    gets added) would keep winning the tie by virtue of being created
    first, since it has no flow of its own to lose by winning. By
    excluding event types nothing actually targets from the contest
    entirely, First Contact can never block a real milestone, while
    real milestones that do collide on the same day still correctly
    take turns per the agent's own priority ranking.

    Ties within a date (equal priority) go to whichever event was
    created first, so the pick is stable and repeatable across runs
    rather than depending on query ordering.
    """
    demand_cache = {}

    def has_demand(event_type):
        if event_type not in demand_cache:
            demand_cache[event_type] = _event_type_has_active_demand(event_type, org)
        return demand_cache[event_type]

    groups = {}
    for event in contact.timeline_events:
        if not has_demand(event.event_type):
            continue
        occurrence_date = _next_occurrence(event, today, window_end)
        if occurrence_date is None:
            continue
        groups.setdefault(occurrence_date, []).append(event)

    winners = {}
    for occurrence_date, events in groups.items():
        best, best_key = None, None
        for event in events:
            tie_key = (-_event_type_priority(contact.owner, event.event_type), event.created_at or datetime.min)
            if best_key is None or tie_key < best_key:
                best, best_key = event, tie_key
        winners[occurrence_date] = best
    return winners


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


def _log_superseded(action, contact, winning_event):
    """Records that a pending suggestion was auto-expired because a
    higher-priority event (see EVENT_TYPE_PRIORITY) showed up for the
    same contact -- distinct wording from _log_expired, which covers a
    suggestion aging out unactioned rather than being outranked.
    Deliberately reuses the existing "action_expired" audit action and
    "expired" status rather than introducing a new one -- from the
    dashboard's point of view both are "this suggestion is gone and
    nobody approved it", just for a different reason, and the summary
    text is what actually distinguishes them for anyone reading
    Recent activity."""
    kind = action.action_type.replace("_", " ")
    winning_label = winning_event.display_label()
    if action.action_type == "gift" and action.suggested_gift_id:
        gift = GiftCatalogItem.query.get(action.suggested_gift_id)
        summary = (
            f"Suggested gift \u2014 {gift.name} \u2014 for {contact.household_name} was "
            f"superseded by {winning_label}, a higher-priority milestone, and auto-expired."
            if gift else
            f"Suggested gift for {contact.household_name} was superseded by {winning_label}, "
            f"a higher-priority milestone, and auto-expired."
        )
    else:
        summary = (
            f"Suggested {kind} for {contact.household_name} was superseded by {winning_label}, "
            f"a higher-priority milestone, and auto-expired."
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


def _expire_superseded_suggestions(contact, winners, org):
    """winners is the {occurrence_date: event} dict from
    _winning_events_for_contact. Any still-pending suggestion whose
    target_date lands on a date where a DIFFERENT event won that day's
    priority contest is for a milestone that's since been outranked --
    expire it rather than leaving it to sit alongside the suggestion
    that actually matters now for that date. This is what handles the
    case _winning_events_for_contact alone doesn't: a lower-priority
    suggestion that was already created and is still pending from an
    earlier run, before the higher-priority event existed or qualified.

    A suggestion whose target_date isn't contested at all this run
    (no entry in winners, or its own event is already the winner for
    that date) is left alone. Scoped to pending only -- anything
    already approved/sent/skipped/expired is left untouched. A
    suggestion whose triggering event was hard-deleted
    (triggering_event_id NULL -- see delete_timeline_event) is left
    alone too: that's its event being gone, not being outranked, and
    the agent should still get to decide what happens to it.
    """
    stale = []
    pending = SuggestedAction.query.filter(
        SuggestedAction.org_id == org.id,
        SuggestedAction.contact_id == contact.id,
        SuggestedAction.status == "pending",
        SuggestedAction.triggering_event_id.isnot(None),
    ).all()
    for action in pending:
        winner = winners.get(action.target_date)
        if winner is not None and winner.id != action.triggering_event_id:
            action.status = "expired"
            action.resolved_at = datetime.utcnow()
            _log_superseded(action, contact, winner)
            stale.append(action)
    return stale


def generate_suggestions_for_org(org, today=None):
    today = today or date.today()
    window_end = today + timedelta(days=LOOKAHEAD_DAYS)
    available_item_ids = {i.id for i in org.available_catalog_items()}

    contacts = Contact.query.filter_by(org_id=org.id).filter(Contact.do_not_contact.is_(False)).all()

    created = []
    for contact in contacts:
        winners = _winning_events_for_contact(contact, today, window_end, org)
        if not winners:
            continue

        _expire_superseded_suggestions(contact, winners, org)

        for occurrence_date, event in winners.items():
            if _suggestion_exists(org.id, event.contact_id, event.id, occurrence_date):
                continue

            gift_trigger = _match_gift_trigger(org.id, event, available_item_ids)
            if gift_trigger is None:
                # No org-specific or global GiftTrigger configured for this
                # event type -- previously this fell back to a contentless
                # "email" suggestion (no gift, no generated_message) that
                # offered nothing actionable; skip instead of creating noise.
                # The Flow/Campaign engine (generate_campaign_suggestions_for_org)
                # is the actual mechanism for AI-assisted outreach now -- this
                # legacy path only still pulls its weight when a real
                # GiftTrigger exists to point it at an actual gift.
                continue
            reason = _build_reason_text(event, occurrence_date, gift_trigger)

            note = None
            if gift_trigger.suggested_gift:
                note = llm.generate_gift_note(event.contact, event, gift_trigger.suggested_gift)

            suggestion = SuggestedAction(
                org_id=org.id,
                contact_id=event.contact_id,
                triggering_event_id=event.id,
                source_campaign_id=None,
                action_type="gift",
                suggested_gift_id=gift_trigger.suggested_gift_id,
                reason_text=reason,
                generated_message=note,
                target_date=occurrence_date,
                status="pending",
            )
            db.session.add(suggestion)
            db.session.flush()  # populate suggestion.id before logging the FK reference
            _log_qualified(suggestion, event.contact)
            created.append(suggestion)

    # Always commit, not just "if created": _expire_superseded_suggestions
    # above can flip existing pending rows to expired even in a run where
    # nothing new gets created for this contact.
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

    # Computed once per contact, not per campaign -- it doesn't depend on
    # which campaign is currently being evaluated. See
    # _winning_events_for_contact for why only events with a real, live
    # flow behind them compete for the day's single winner -- an event
    # type nothing targets (e.g. the auto-seeded First Contact event)
    # never enters the contest, so it can't block a real milestone that
    # does have a flow.
    winning_events_cache = {}

    def winning_events_for(contact):
        if contact.id not in winning_events_cache:
            winners = _winning_events_for_contact(contact, today, window_end, org)
            if winners:
                _expire_superseded_suggestions(contact, winners, org)
            winning_events_cache[contact.id] = winners
        return winning_events_cache[contact.id]

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
            winners = winning_events_for(contact)
            matching_events = [e for e in winners.values() if e.event_type == campaign.event_type]
            if not matching_events:
                # None of this contact's date-group winners this cycle
                # belong to this campaign's milestone type -- this
                # campaign sits out the run for them entirely.
                continue

            # How many more times this campaign is allowed to fire for
            # this contact, or None for unlimited. repeat_enabled=False
            # collapses to "fire at most once, ever" (cap=1), same as
            # before this feature existed; a numeric max_occurrences
            # further caps a repeating flow. Computed once per contact
            # (not per event/occurrence) since it's an existing count
            # against the DB.
            cap = _campaign_occurrence_cap(campaign)
            occurrence_count = _campaign_occurrence_count_for_contact(campaign.id, contact.id) if cap is not None else None

            for event in matching_events:
                for trigger_date in _campaign_trigger_dates(event, campaign, today, window_end):
                    if cap is not None and occurrence_count >= cap:
                        break  # this contact has hit its cap -- no more occurrences for this campaign

                    if _campaign_suggestion_exists(org.id, campaign.id, contact.id, event.id, trigger_date):
                        continue

                    if not campaign_rules.evaluate_conditions(campaign, contact, org, today, event=event):
                        continue

                    gift_item, gift_reasoning = None, None
                    if campaign.action_type == "gift":
                        gift_item, gift_reasoning = _resolve_campaign_gift(campaign, contact, available_item_ids)

                    message = None
                    if campaign.action_type in ("email", "text", "handwritten_note"):
                        message = _resolve_campaign_message(campaign, contact, event)
                    elif campaign.action_type == "gift" and gift_item:
                        message = _resolve_gift_note(campaign, contact, event, gift_item)

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
                    try:
                        # Nested (SAVEPOINT) so a loss here only unwinds
                        # this one insert, not every suggestion already
                        # added earlier in this same run -- see
                        # SuggestedAction.uq_suggested_action_campaign_occurrence
                        # for why this can legitimately fail: another
                        # near-simultaneous run (a second gunicorn worker,
                        # or the nightly job overlapping an on-demand
                        # dashboard-triggered run) can win the same
                        # check-then-insert race and commit first.
                        with db.session.begin_nested():
                            db.session.add(suggestion)
                            db.session.flush()  # populate suggestion.id before logging the FK reference
                    except IntegrityError:
                        continue
                    _log_qualified(suggestion, contact)
                    created.append(suggestion)
                    if cap is not None:
                        occurrence_count += 1

    # Always commit, not just "if created": see the matching comment in
    # generate_suggestions_for_org.
    db.session.commit()
    return created


def _campaign_occurrence_cap(campaign):
    """The max number of suggestions this campaign should ever produce
    for one contact, or None for unlimited. repeat_enabled=False means
    'fire at most once, ever' regardless of what max_occurrences holds
    (mirrors the pre-existing behavior); repeat_enabled=True defers to
    max_occurrences (NULL there also means unlimited)."""
    if not campaign.repeat_enabled:
        return 1
    return campaign.max_occurrences


def _campaign_occurrence_count_for_contact(campaign_id, contact_id):
    """How many suggestions this campaign has already generated for
    this contact, of ANY status, regardless of which occurrence
    triggered each one -- compared against _campaign_occurrence_cap to
    decide whether it's allowed to fire again."""
    return SuggestedAction.query.filter_by(
        source_campaign_id=campaign_id, contact_id=contact_id,
    ).count()


def _campaign_trigger_date(event, direction, amount, unit, today, window_end):
    """The single, first-in-window trigger date for a campaign/recipe's
    timing configuration -- used by the Preview step, which
    deliberately only ever shows one upcoming occurrence per event (see
    preview_flow_matches) and doesn't model a flow's own repeat
    schedule. For the live generation path, see _campaign_trigger_dates
    below, which layers repeat_enabled/recur_interval on top of this
    same offset math.

    Month/year units use calendar arithmetic (relativedelta) so '1 year
    after' lands on the same calendar date next year rather than +365
    raw days -- important for an annual closing anniversary to keep
    landing on the actual closing date. Handles an offset that pushes a
    recurring event's occurrence across a year boundary by checking
    last/this/next year's occurrence, not just 'this year'."""
    def apply_offset(base_date):
        return _apply_timing_offset(base_date, direction, amount, unit)

    if not event.is_recurring:
        trigger_date = apply_offset(event.event_date)
        return trigger_date if today <= trigger_date <= window_end else None

    for year_delta in (-1, 0, 1):
        try:
            base = event.event_date.replace(year=today.year + year_delta)
        except ValueError:
            base = event.event_date.replace(year=today.year + year_delta, day=28)  # Feb 29 -> Feb 28
        trigger_date = apply_offset(base)
        if today <= trigger_date <= window_end:
            return trigger_date
    return None


def _apply_timing_offset(base_date, direction, amount, unit):
    if direction == "same_day":
        return base_date
    signed = amount if direction == "after" else -amount
    if unit == "day":
        return base_date + timedelta(days=signed)
    if unit == "week":
        return base_date + timedelta(weeks=signed)
    if unit == "month":
        return base_date + relativedelta(months=signed)
    return base_date + relativedelta(years=signed)  # unit == "year"


def _campaign_trigger_dates(event, campaign, today, window_end):
    """All of this campaign's trigger dates for one event that fall in
    [today, window_end] -- the live-generation counterpart to
    _campaign_trigger_date above, which only ever returns one.

    For an event type that already recurs on the calendar (a birthday,
    an anniversary -- see TimelineEvent.is_recurring), this is just the
    usual before/same-day/after offset applied to whichever yearly
    occurrence(s) land in the window, same as before this feature
    existed.

    For a one-time event (e.g. a home closing), there's no natural
    recurrence to ride on, so when repeat_enabled is on the campaign
    supplies its OWN schedule: recur_interval_amount/recur_interval_unit
    applied repeatedly from the first trigger date onward (default
    every 1 year). This is what lets a one-time closing date still
    produce an annual closing-anniversary gift. max_occurrences isn't
    checked here -- that's a per-contact running count the caller
    enforces across events, not something a single event's date list
    can know about on its own."""
    def apply_offset(base_date):
        return _apply_timing_offset(base_date, campaign.timing_direction, campaign.timing_amount, campaign.timing_unit)

    if event.is_recurring:
        dates = []
        for year_delta in (-1, 0, 1):
            try:
                base = event.event_date.replace(year=today.year + year_delta)
            except ValueError:
                base = event.event_date.replace(year=today.year + year_delta, day=28)  # Feb 29 -> Feb 28
            trigger_date = apply_offset(base)
            if today <= trigger_date <= window_end:
                dates.append(trigger_date)
        return dates

    anchor = apply_offset(event.event_date)
    if not campaign.repeat_enabled:
        return [anchor] if today <= anchor <= window_end else []

    interval_amount = campaign.recur_interval_amount or 1
    interval_unit = campaign.recur_interval_unit or "year"
    return _repeat_occurrences(anchor, interval_amount, interval_unit, today, window_end)


def _repeat_occurrences(anchor, amount, unit, today, window_end):
    """Occurrence dates (anchor, anchor + 1 interval, anchor + 2
    intervals, ...) that fall within [today, window_end]. Jumps close
    to 'today' first via direct arithmetic instead of walking forward
    one interval at a time from the anchor, so an anchor from years ago
    combined with a short interval (e.g. a closing date from 2019,
    repeating every week) doesn't require thousands of loop iterations
    -- only a small, bounded number of steps run once we're near the
    window."""
    if amount <= 0:
        amount = 1

    k = 0
    if anchor < today:
        if unit == "day":
            elapsed = (today - anchor).days
        elif unit == "week":
            elapsed = (today - anchor).days // 7
        elif unit == "month":
            elapsed = (today.year - anchor.year) * 12 + (today.month - anchor.month)
        else:  # year
            elapsed = today.year - anchor.year
        k = max(elapsed // amount - 1, 0)  # step back one interval to be safe around boundaries

    dates = []
    for _ in range(64):  # bounded walk -- the window is only LOOKAHEAD_DAYS wide
        occurrence = _advance_date(anchor, amount, unit, k)
        if occurrence > window_end:
            break
        if occurrence >= today:
            dates.append(occurrence)
        k += 1
    return dates


def _advance_date(anchor, amount, unit, k):
    total = amount * k
    if unit == "day":
        return anchor + timedelta(days=total)
    if unit == "week":
        return anchor + timedelta(weeks=total)
    if unit == "month":
        return anchor + relativedelta(months=total)
    return anchor + relativedelta(years=total)  # unit == "year"


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


def _resolve_gift_note(campaign, contact, event, gift_item):
    """The note attached to a gift/gift-card action, or None if the
    flow has notes turned off. A fixed note_text (same placeholder
    convention as message_template) takes precedence; left blank, the
    LLM writes the note fresh -- today's original behavior."""
    if not campaign.add_note:
        return None
    if campaign.note_text:
        return campaign.note_text.format(
            contact_name=contact.household_name,
            event_label=event.display_label(),
            event_date=event.event_date.strftime("%b %-d, %Y"),
        )
    return llm.generate_gift_note(contact, event, gift_item)


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
    CampaignRecipe both already have: name, event_type,
    timing_direction, timing_amount, timing_unit, repeat_enabled,
    rules (a list of objects with .field/.config, or real
    CampaignRule/CampaignRecipeRule rows), price_max_cents,
    use_llm_gift_selection, action_type, suggested_gift_id,
    use_llm_copy, message_template, llm_prompt_hint.

    This makes REAL LLM calls when use_llm_gift_selection/use_llm_copy
    are set -- it's a genuine dry run of what would be generated, not a
    mock, so it costs the same as a real suggestion would.

    Does not check for already-existing suggestions, or repeat_enabled
    (there's nothing to dedupe against, or repeat, for a flow that
    isn't live yet) -- it shows every match in the lookahead window,
    capped at `limit` results.
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

        matching_events = [e for e in contact.timeline_events if e.event_type == spec.event_type]
        for event in matching_events:
            if len(results) >= limit:
                break
            trigger_date = _campaign_trigger_date(
                event, spec.timing_direction, spec.timing_amount, spec.timing_unit, today, window_end,
            )
            if trigger_date is None:
                continue

            if not campaign_rules.evaluate_conditions(spec, contact, org, today, event=event):
                continue

            gift_item, gift_reasoning = None, None
            if spec.action_type == "gift":
                gift_item, gift_reasoning = _resolve_campaign_gift(spec, contact, available_item_ids)

            message = None
            if spec.action_type in ("email", "text", "handwritten_note"):
                message = _resolve_campaign_message(spec, contact, event)
            elif spec.action_type == "gift" and gift_item:
                message = _resolve_gift_note(spec, contact, event, gift_item)

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
