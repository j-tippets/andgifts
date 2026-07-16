"""
Registry of campaign rule types.

The split that matters: WHICH rules are attached to a given campaign,
and with what parameters, is pure data (CampaignRule / CampaignRecipeRule
rows) -- turning a rule on/off, changing a threshold, stacking three
rules on one campaign, all need zero deploys. But the logic behind a
rule_type -- what "once per contact" or "cooldown" actually checks
against the database -- is code, registered here. Adding a genuinely
new rule TYPE is still a small, contained code change (one function +
one registry entry + one builder-UI card), not a rebuild of the
campaign system.

Two different shapes of rule, and only one is handled by this module:

- Per-contact predicates (what's here): "does this one contact, right
  now, satisfy this rule" -- evaluated once per (contact, event) pair
  inside the normal suggestion-generation loop. interest_tag and
  once_per_contact are both this shape.

- Batch-level constraints (NOT modeled yet -- intentionally): things
  like "stop after $500 spent this month" or "never generate more than
  50 suggestions in one run" apply to the whole set of matches, not to
  one contact in isolation, and need a different hook -- e.g. filtering
  the candidate list after it's built, or a running total checked
  mid-loop. When we build the first one of these, design that hook
  deliberately rather than forcing it through evaluate_rules() below.
"""
from datetime import timedelta


def _eval_once_per_contact(contact, event, campaign, org, today, config):
    """True if this campaign has never generated a suggestion for this
    contact before -- of ANY status (pending, approved, skipped) and
    regardless of which of the contact's events triggered it. This is
    different from the dedup that already runs unconditionally for
    every campaign (_campaign_suggestion_exists in suggestion_engine.py),
    which only blocks the SAME event occurrence from firing twice --
    a contact with two closing-type events could still get two gifts
    under that dedup alone. This rule closes that gap when an agent
    wants a true "only ever once" flow."""
    from app.models import SuggestedAction
    from app.extensions import db

    return not db.session.query(
        SuggestedAction.query.filter_by(
            source_campaign_id=campaign.id,
            contact_id=contact.id,
        ).exists()
    ).scalar()


def _eval_cooldown_days(contact, event, campaign, org, today, config):
    """True if this contact hasn't received a suggestion from ANY of
    the org's campaigns (not just this one) in the last N days --
    config: {"days": 90}. Guards against gift fatigue when several
    campaigns can independently match the same contact around the
    same time."""
    from app.models import SuggestedAction
    from app.extensions import db

    days = config.get("days")
    if not days:
        return True  # misconfigured rule shouldn't block every suggestion

    cutoff = today - timedelta(days=int(days))
    return not db.session.query(
        SuggestedAction.query.filter(
            SuggestedAction.org_id == org.id,
            SuggestedAction.contact_id == contact.id,
            SuggestedAction.target_date >= cutoff,
            SuggestedAction.target_date <= today,
        ).exists()
    ).scalar()


RULE_TYPES = {
    "once_per_contact": {
        "label": "Only trigger once per contact, ever",
        "description": "Skips this contact if this flow has already generated a suggestion for them before, no matter which of their events triggered it.",
        "config_schema": {},
        "evaluate": _eval_once_per_contact,
    },
    "cooldown_days": {
        "label": "Cooldown since last touch",
        "description": "Skips this contact if they've gotten a suggestion from any flow within the last N days.",
        "config_schema": {"days": int},
        "evaluate": _eval_cooldown_days,
    },
}


def evaluate_rules(campaign, contact, event, org, today):
    """True if every per-contact rule attached to this campaign passes
    for this contact/event. Call this inside the suggestion-generation
    loop, after the trigger/timing check and before creating the
    SuggestedAction."""
    import logging

    for rule in campaign.rules:
        spec = RULE_TYPES.get(rule.rule_type)
        if spec is None:
            # Fail open, not closed: an unrecognized rule_type (e.g. a
            # rule type that got removed from the registry, or a row
            # written by a future version of this code) shouldn't
            # silently block every suggestion for every contact. Log it
            # so it gets noticed and cleaned up.
            logging.getLogger(__name__).warning(
                "Unknown campaign rule_type %r on campaign %s -- skipping it.",
                rule.rule_type, campaign.id,
            )
            continue
        if not spec["evaluate"](contact, event, campaign, org, today, rule.config or {}):
            return False
    return True
