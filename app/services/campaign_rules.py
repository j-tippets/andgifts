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
    "interest_tag": {
        "label": "Contact has interest tag",
        "value_type": "text",
        "operators": ["equals", "not_equals"],
    },
    "gift_cooldown_days": {
        "label": "Days since their last suggestion (any flow)",
        "value_type": "number",
        "operators": ["older_than"],
    },
}

# Which operators make sense for each custom-field type. Date fields are
# intentionally left out of the condition builder for now -- "older
# than" already covers the one date-shaped comparison that comes up in
# practice (gift_cooldown_days above); a general date condition can be
# added here later without touching anything else.
OPERATORS_BY_VALUE_TYPE = {
    "text": ["equals", "not_equals", "contains", "is_empty", "is_not_empty"],
    "number": ["equals", "not_equals", "greater_than", "less_than", "is_empty", "is_not_empty"],
    "checkbox": ["equals"],
}

OPERATOR_LABELS = {
    "equals": "is",
    "not_equals": "is not",
    "contains": "contains",
    "greater_than": "is greater than",
    "less_than": "is less than",
    "older_than": "is more than",
    "is_empty": "is empty",
    "is_not_empty": "is not empty",
}


def condition_field_choices(org):
    """(field_key, label, value_type) tuples for the condition builder's
    field dropdown: built-ins first, then this org's own custom fields
    (org-scope and every agent's personal ones -- a flow's conditions
    aren't scoped per-agent the way personal custom fields otherwise
    are, since the flow itself already belongs to one agent or is a
    shared team template)."""
    from app.models import CustomFieldDefinition

    choices = [
        (key, spec["label"], spec["value_type"]) for key, spec in BUILT_IN_FIELDS.items()
    ]
    custom_fields = (
        CustomFieldDefinition.query.filter_by(org_id=org.id)
        .filter(CustomFieldDefinition.field_type.in_(["text", "number", "checkbox", "select"]))
        .order_by(CustomFieldDefinition.label)
        .all()
    )
    for f in custom_fields:
        value_type = "text" if f.field_type in ("text", "select") else f.field_type
        choices.append((f"custom:{f.id}", f.label, value_type))
    return choices


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
        value_type = "text" if definition.field_type in ("text", "select") else definition.field_type
        return OPERATORS_BY_VALUE_TYPE.get(value_type, [])
    return []


def _actual_value(field_key, contact):
    """The contact's current value for a condition field, or a sentinel
    tuple (False, None) for fields that need special per-condition
    handling (gift_cooldown_days -- it depends on `today`/`org`, not
    just the contact, so it's evaluated directly in evaluate_conditions
    instead of through this generic path). Returns (True, value)
    otherwise, so an actual None/empty value is distinguishable from
    "not handled here"."""
    if field_key == "interest_tag":
        return True, {i.name for i in contact.interests}
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

    if isinstance(actual, set):
        if operator == "equals":
            return expected in actual
        if operator == "not_equals":
            return expected not in actual
        return False

    if operator in ("equals", "not_equals"):
        matches = (actual or "").strip().lower() == (expected or "").strip().lower()
        return matches if operator == "equals" else not matches
    if operator == "contains":
        return (expected or "").strip().lower() in (actual or "").lower()

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


def evaluate_conditions(campaign, contact, org, today):
    """True if every condition attached to this campaign passes for
    this contact (plain AND). Call this inside the suggestion-generation
    loop, after the trigger/timing check and before creating the
    SuggestedAction."""
    import logging

    for rule in campaign.rules:
        field_key = rule.field
        operator = (rule.config or {}).get("operator")
        expected = (rule.config or {}).get("value")

        if field_key == "gift_cooldown_days":
            if not _eval_cooldown(contact, org, today, expected):
                return False
            continue

        handled, actual = _actual_value(field_key, contact)
        if not handled:
            logging.getLogger(__name__).warning(
                "Unknown condition field %r on campaign %s -- skipping it.",
                field_key, campaign.id,
            )
            continue
        if not _compare(operator, actual, expected):
            return False
    return True
