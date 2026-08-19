"""
Generic condition engine for Flows (Campaign / CampaignRecipe).

A condition is (field, operator, value), stored as one CampaignRule /
CampaignRecipeRule row -- `field` is the DB column still named
`rule_type` (see app/models/campaigns.py), `config` holds
{"operator": ..., "value": ...}. All conditions on a flow are ANDed
together; the schema doesn't rule out OR/nested groups later, but nothing
in this module builds that yet (matches the MVP scope we agreed on).

Two kinds of field:
- Built into BUILT_IN_FIELDS below: things every org has regardless of
  their own setup (interest tags, a cross-flow gift cooldown).
- An org's own custom fields (Contact custom fields -- see
  app/models/contact.py CustomFieldDefinition), addressed as
  "custom:<field_definition_id>". This is deliberately how a flow
  reaches something like "transaction value" or "property type": &Gifts
  doesn't have a first-class Transaction/Deal model, so those live as
  agent-defined custom fields on Contact, not as a fabricated built-in.

Adding a genuinely new BUILT-IN field is a small code change here (one
entry in BUILT_IN_FIELDS + a branch in _actual_value). Adding a new
custom field needs no code change at all -- it's just a new
CustomFieldDefinition row, and every flow's condition builder picks it
up automatically via condition_field_choices().
"""
from datetime import timedelta


BUILT_IN_FIELDS = {
    # These two deliberately don't reuse OPERATORS_BY_VALUE_TYPE's
    # "text" list even though value_type is "text" -- their value box
    # is always a closed dropdown of exact tag/badge names (see
    # condition_field_choices below), not free text, so "contains"
    # doesn't make sense here the way it does for a real free-typed
    # text field. is_empty/is_not_empty still apply ("has no badges
    # at all" / "has at least one interest tag").
    "interest_tag": {
        "label": "Contact has interest tag",
        "value_type": "text",
        "operators": ["equals", "not_equals", "is_empty", "is_not_empty"],
    },
    "has_badge": {
        "label": "Contact has badge",
        "value_type": "text",
        "operators": ["equals", "not_equals", "is_empty", "is_not_empty"],
    },
    "gift_cooldown_days": {
        "label": "Days since their last suggestion (any flow)",
        "value_type": "number",
        "operators": ["older_than"],
    },
    # The dollar amount on whichever TimelineEvent is driving this
    # flow's current trigger (see TimelineEvent.amount_cents) -- e.g.
    # "sale price greater than $200,000" on a flow anchored to the
    # "closing" event_type. Only meaningful in that per-event context,
    # so it's evaluated via the `event` param threaded through
    # evaluate_conditions/_actual_value below rather than off the
    # contact alone like every other built-in.
    "event_amount": {
        "label": "This event's amount",
        "value_type": "number",
        "operators": ["greater_than", "less_than", "equals", "not_equals", "is_empty", "is_not_empty"],
    },
}

# Which operators make sense for each custom-field type. interest_tag
# and has_badge (above) deliberately do NOT pull from this table even
# though their value_type is "text" -- their value box is always a
# closed dropdown of exact tag/badge names (see condition_field_choices),
# not free text, so "contains" doesn't make sense there the way it does
# for a genuinely free-typed custom text field; they get their own
# explicit operator list instead.
OPERATORS_BY_VALUE_TYPE = {
    "text": ["contains", "not_contains", "equals", "not_equals", "is_empty", "is_not_empty"],
    "number": ["greater_than", "less_than", "equals", "not_equals", "is_empty", "is_not_empty"],
    "date": ["before", "on", "after", "is_empty", "is_not_empty"],
    "checkbox": ["is_checked", "is_not_checked"],
}

OPERATOR_LABELS = {
    "equals": "equals",
    "not_equals": "does not equal",
    "contains": "contains",
    "not_contains": "does not contain",
    "greater_than": "is greater than",
    "less_than": "is less than",
    "older_than": "is more than",
    "before": "is before",
    "on": "is on",
    "after": "is after",
    "is_checked": "is checked",
    "is_not_checked": "is not checked",
    "is_empty": "is empty",
    "is_not_empty": "is not empty",
}

# Operators that don't take a value -- everything else needs one, and a
# saved condition with a blank value doesn't error, it just can never
# match anyone (e.g. "interest tag equals ''" checks membership in a
# set, and "" is never a member). See _conditions_from_form. The
# condition builder's JS also hides/disables the value box for any
# operator in this set, since asking for a value nobody can fill in
# meaningfully (what would you type for "is checked"?) is confusing.
VALUE_LESS_OPERATORS = {"is_empty", "is_not_empty", "is_checked", "is_not_checked"}


def condition_field_choices(org):
    """(field_key, label, value_type, options) tuples for the condition
    builder's field dropdown: built-ins first, then this org's own
    custom fields (org-scope and every agent's personal ones -- a
    flow's conditions aren't scoped per-agent the way personal custom
    fields otherwise are, since the flow itself already belongs to one
    agent or is a shared team template).

    `options` is a list of (value, label) pairs when the field has a
    fixed, known set of valid values -- badges, interest tags,
    select-type custom fields, checkbox (Yes/No) -- so the condition
    builder can render a dropdown instead of a free-text box the agent
    has to guess the exact spelling for (e.g. does this badge say
    "VIP" or "Vip"?). Empty list means free text/number -- there's
    nothing to enumerate for something like a numeric "Income" field,
    so that stays a text box the agent types a threshold into."""
    from app.models import CustomFieldDefinition, Badge, Interest

    choices = []
    for key, spec in BUILT_IN_FIELDS.items():
        if key == "interest_tag":
            names = sorted({i.name for i in Interest.query.all()}, key=lambda n: n.lower())
            options = [(n, n) for n in names]
        elif key == "has_badge":
            visible_badges = Badge.query.filter(
                (Badge.org_id.is_(None)) | (Badge.org_id == org.id)
            ).all()
            labels = sorted({b.label for b in visible_badges}, key=lambda n: n.lower())
            options = [(l, l) for l in labels]
        else:
            options = []
        choices.append((key, spec["label"], spec["value_type"], options))

    custom_fields = (
        CustomFieldDefinition.query.filter_by(org_id=org.id)
        .filter(CustomFieldDefinition.field_type.in_(
            ["text", "textarea", "number", "currency", "date", "checkbox", "select"]
        ))
        .order_by(CustomFieldDefinition.label)
        .all()
    )
    for f in custom_fields:
        value_type = _custom_field_value_type(f.field_type)
        choices.append((f"custom:{f.id}", f.label, value_type, _custom_field_options(f)))
    return choices


def _custom_field_options(field):
    """(value, label) pairs for a CustomFieldDefinition with a fixed
    set of valid values -- empty list for anything freeform (text,
    number, currency) and for checkbox, whose only operators
    (is_checked/is_not_checked) are value-less so there's nothing to
    pick from a dropdown for."""
    if field.field_type == "select":
        return [(opt, opt) for opt in field.option_list()]
    return []


def _custom_field_value_type(field_type):
    """Which condition-builder value_type a CustomFieldDefinition.field_type
    maps to. currency reuses the "number" comparators as-is -- the value
    is stored as a plain numeric string same as "number" fields, dollar
    formatting is a display-only concern in the templates. textarea
    reuses "text" (contains/equals/etc. all still make sense on a longer
    free-typed field). date and checkbox pass straight through to their
    own entries in OPERATORS_BY_VALUE_TYPE."""
    if field_type in ("text", "select", "textarea"):
        return "text"
    if field_type == "currency":
        return "number"
    return field_type


def operators_for_field(field_key, org):
    """Which operators are valid for this field key -- used both to
    populate the operator dropdown and to validate a submitted
    condition server-side."""
    spec = BUILT_IN_FIELDS.get(field_key)
    if spec:
        return spec["operators"]
    if field_key.startswith("custom:"):
        from app.models import CustomFieldDefinition

        field_id = field_key.split(":", 1)[1]
        definition = CustomFieldDefinition.query.filter_by(id=field_id, org_id=org.id).first()
        if not definition:
            return []
        value_type = _custom_field_value_type(definition.field_type)
        return OPERATORS_BY_VALUE_TYPE.get(value_type, [])
    return []


def _actual_value(field_key, contact, event=None):
    """The contact's current value for a condition field, or a sentinel
    tuple (False, None) for fields that need special per-condition
    handling (gift_cooldown_days -- it depends on `today`/`org`, not
    just the contact, so it's evaluated directly in evaluate_conditions
    instead of through this generic path). Returns (True, value)
    otherwise, so an actual None/empty value is distinguishable from
    "not handled here".

    `event` is the specific TimelineEvent driving this trigger occurrence
    (both call sites in suggestion_engine.py already have it in scope),
    needed only for event_amount -- every other field is contact-level."""
    if field_key == "interest_tag":
        return True, {i.name for i in contact.interests}
    if field_key == "has_badge":
        return True, {b.label for b in contact.badges}
    if field_key == "event_amount":
        amount = event.amount_cents if event and event.amount_cents is not None else None
        return True, (str(amount / 100) if amount is not None else None)
    if field_key.startswith("custom:"):
        field_id = field_key.split(":", 1)[1]
        row = next((v for v in contact.custom_values if v.field_definition_id == field_id), None)
        return True, (row.value if row else None)
    return False, None


def _compare(operator, actual, expected):
    """Generic comparator. `actual` is either a string/None (most
    fields) or a set of strings (interest_tag, membership-style)."""
    if operator == "is_empty":
        return not actual
    if operator == "is_not_empty":
        return bool(actual)
    # Checkbox custom fields store "1"/"0" (see
    # contacts._save_custom_field_values) -- anything else (missing
    # value, "0") counts as unchecked.
    if operator == "is_checked":
        return actual == "1"
    if operator == "is_not_checked":
        return actual != "1"

    if isinstance(actual, set):
        if operator == "equals":
            return expected in actual
        if operator == "not_equals":
            return expected not in actual
        return False

    if operator in ("equals", "not_equals"):
        matches = (actual or "").strip().lower() == (expected or "").strip().lower()
        return matches if operator == "equals" else not matches
    if operator in ("contains", "not_contains"):
        is_in = (expected or "").strip().lower() in (actual or "").lower()
        return is_in if operator == "contains" else not is_in

    if operator in ("before", "on", "after"):
        # Date custom fields store an ISO "YYYY-MM-DD" string (see
        # contacts/_custom_fields_macro.html's <input type="date">).
        from datetime import date

        try:
            actual_date = date.fromisoformat(actual)
            expected_date = date.fromisoformat(expected)
        except (TypeError, ValueError):
            return False
        if operator == "before":
            return actual_date < expected_date
        if operator == "on":
            return actual_date == expected_date
        return actual_date > expected_date

    # Numeric comparisons -- a blank/non-numeric actual value never
    # satisfies a greater/less-than condition (fails closed, not open).
    try:
        actual_num = float(actual)
        expected_num = float(expected)
    except (TypeError, ValueError):
        return False
    if operator == "greater_than":
        return actual_num > expected_num
    if operator == "less_than":
        return actual_num < expected_num
    return False


def _eval_cooldown(contact, org, today, expected_days):
    """True if this contact has NOT gotten a suggestion from any of the
    org's flows within expected_days -- the one field that needs
    database context beyond the contact itself."""
    from app.models import SuggestedAction
    from app.extensions import db

    try:
        days = int(expected_days)
    except (TypeError, ValueError):
        return True  # misconfigured condition shouldn't block every suggestion

    cutoff = today - timedelta(days=days)
    return not db.session.query(
        SuggestedAction.query.filter(
            SuggestedAction.org_id == org.id,
            SuggestedAction.contact_id == contact.id,
            SuggestedAction.target_date >= cutoff,
            SuggestedAction.target_date <= today,
        ).exists()
    ).scalar()


def evaluate_conditions(campaign, contact, org, today, event=None):
    """True if every condition attached to this campaign passes for
    this contact (plain AND). Call this inside the suggestion-generation
    loop, after the trigger/timing check and before creating the
    SuggestedAction.

    `event` is the specific TimelineEvent occurrence this trigger check
    is for -- pass it whenever the caller has one (both current call
    sites do) so the event_amount condition field can read it. Every
    other field ignores it."""
    import logging

    for rule in campaign.rules:
        field_key = rule.field
        operator = (rule.config or {}).get("operator")
        expected = (rule.config or {}).get("value")

        if field_key == "gift_cooldown_days":
            if not _eval_cooldown(contact, org, today, expected):
                return False
            continue

        handled, actual = _actual_value(field_key, contact, event)
        if not handled:
            logging.getLogger(__name__).warning(
                "Unknown condition field %r on campaign %s -- skipping it.",
                field_key, campaign.id,
            )
            continue
        if not _compare(operator, actual, expected):
            return False
    return True
