"""
LLM-driven campaign steps: picking the best gift for a contact, and
writing the customer-facing message body for email/text/handwritten_note
actions. Both call the real Anthropic API when ANTHROPIC_API_KEY is
configured, and fall back to a deterministic rule-based result if the
key is missing OR the call fails for any reason (timeout, rate limit,
bad response) -- campaign suggestion generation should never break
because an LLM call had a bad day.
"""
import json
from flask import current_app

MODEL = "claude-haiku-4-5-20251001"


def _client():
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except Exception:
        return None


def _extract_text(response):
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


def _strip_json_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def _practice_type_label(contact):
    """The org's assigned business type (Real Estate, Law Firm, Dental,
    ...) -- see Org.practice_type / PracticeType -- so prompts don't
    hard-code an assumption that every org is a real estate practice
    (or that whoever's sending the gift holds a specific job title
    like "agent", which isn't true for every seat on a team -- an
    executive assistant sending on an agent's behalf, for instance).
    Falls back to a neutral "business" when the org hasn't set a
    practice type yet, or -- as with campaigns.preview_message's
    made-up contact -- there's no real org attached at all."""
    org = getattr(contact, "org", None)
    practice_type = getattr(org, "practice_type", None) if org else None
    return practice_type.name if practice_type else "business"


def pick_gift(contact, candidates):
    """Returns (GiftCatalogItem or None, reasoning str or None) -- the
    best gift for this contact from `candidates` (already filtered by
    the caller to the org's available catalog and any price cap)."""
    if not candidates:
        return None, None

    client = _client()
    if client is not None:
        try:
            interests = ", ".join(i.name for i in contact.interests) or "no known interests on file"
            options = "\n".join(
                f"- id={c.id}: {c.name} (${c.price_cents / 100:.2f}) -- tags: {c.interest_tags or 'none'}"
                for c in candidates
            )
            prompt = (
                f"A {_practice_type_label(contact)} professional's client has these interests: "
                f"{interests}.\n\nChoose the single best gift for them from this list:\n{options}\n\n"
                "Respond with ONLY a JSON object, no markdown formatting, no preamble:\n"
                '{"item_id": "<the id>", "reasoning": "<one short sentence explaining why>"}'
            )
            response = client.messages.create(
                model=MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(_strip_json_fences(_extract_text(response)))
            chosen = next((c for c in candidates if c.id == data.get("item_id")), None)
            if chosen is not None:
                return chosen, data.get("reasoning")
        except Exception:
            pass  # fall through to the heuristic below

    return _heuristic_pick_gift(contact, candidates), None


def _heuristic_pick_gift(contact, candidates):
    """Deterministic stand-in: most interest-tag overlap wins, cheapest
    breaks ties. Used when no API key is set or the API call fails."""
    contact_interests = {i.name.lower() for i in contact.interests}

    def score(item):
        tags = {t.lower() for t in item.tag_list()}
        return len(tags & contact_interests)

    ranked = sorted(candidates, key=lambda c: (-score(c), c.price_cents))
    return ranked[0] if ranked else None


def find_matching_gifts(description, items, max_results=3):
    """Free-form natural-language gift search (the "explain the situation"
    box on the per-contact Send a gift page) -- takes something like "my
    friend just lost her mom, good friend but not close friend" and
    returns up to `max_results` items from `items` (already scoped by the
    caller to the org's available + active catalog) ranked best first,
    each with a short reasoning tied to what was actually described.

    Returns (matches, used_ai) where matches is a list of dicts
    {"item": GiftCatalogItem, "reasoning": str}, possibly empty if
    nothing in the catalog is a good fit -- callers should show that
    honestly rather than forcing a bad match. used_ai is False when this
    fell back to the keyword heuristic (no API key, or the call failed),
    so the caller can be upfront about that too.
    """
    description = (description or "").strip()
    if not description or not items:
        return [], False

    client = _client()
    if client is not None:
        try:
            options = "\n".join(
                f"- id={i.id}: {i.name} (${i.price_cents / 100:.2f}, ships in {i.lead_time_days} days) "
                f"-- occasion: {i.occasion or 'none'} -- tags: {i.interest_tags or 'none'} "
                f"-- {i.description or 'no description'}"
                for i in items
            )
            prompt = (
                f"Someone is choosing a gift and described their situation like this:\n"
                f'"{description}"\n\n'
                f"Here is the available gift catalog:\n{options}\n\n"
                f"Pick up to {max_results} gifts from the list above that genuinely fit this "
                "situation, best match first. Use judgment about tone -- a relationship "
                "described as not close shouldn't get an overly intimate/expensive gift, a "
                "loss or hardship calls for something comforting rather than celebratory, "
                "and so on. If nothing in the list is actually appropriate, return fewer "
                "results or an empty list rather than forcing a weak match.\n\n"
                "Respond with ONLY a JSON object, no markdown formatting, no preamble:\n"
                '{"matches": [{"item_id": "<id>", "reasoning": "<one short sentence tied to '
                'their specific situation>"}]}'
            )
            response = client.messages.create(
                model=MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(_strip_json_fences(_extract_text(response)))
            by_id = {i.id: i for i in items}
            matches = []
            for m in data.get("matches", [])[:max_results]:
                item = by_id.get(m.get("item_id"))
                if item is not None:
                    matches.append({"item": item, "reasoning": m.get("reasoning")})
            return matches, True
        except Exception:
            pass  # fall through to the keyword heuristic below

    return _heuristic_find_gifts(description, items, max_results), False


def _heuristic_find_gifts(description, items, max_results):
    """Keyword-overlap stand-in for find_matching_gifts, used when no API
    key is set or the call fails -- can't do real semantic matching
    without the LLM, so this just scores each item by how many of the
    description's words appear in its name/occasion/description/tags,
    and is upfront in its reasoning that that's all it did."""
    words = {w for w in description.lower().split() if len(w) > 3}
    if not words:
        return []

    def score_and_hits(item):
        text = " ".join(filter(None, [
            item.name, item.occasion, item.description, item.interest_tags,
        ])).lower()
        hits = {w for w in words if w in text}
        return len(hits), hits

    scored = [(item, *score_and_hits(item)) for item in items]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda s: -s[1])

    return [
        {
            "item": item,
            "reasoning": f"Matched on: {', '.join(sorted(hits))}.",
        }
        for item, count, hits in scored[:max_results]
    ]


def generate_gift_note(contact, event, gift_item, prompt_hint=None):
    """Returns a short note to go with a gift suggestion -- something the
    agent can attach to a physical gift or, later, send along with an
    e-gift-card delivery -- explaining what it's for. Same fallback
    contract as generate_message: real API call when a key is configured,
    a plain template otherwise.

    event is optional -- None for a manually-placed one-off order
    (routes/contacts.new_order), which isn't tied to any particular
    timeline event the way an automated flow/suggestion always is.
    The prompt and fallback both drop the "for their {event}" phrasing
    entirely rather than inventing an occasion that isn't there."""
    client = _client()
    if client is not None:
        try:
            gift_desc = f" ({gift_item.name})" if gift_item else ""
            occasion = f" for their {event.display_label()}" if event else ""
            prompt = (
                f"Write a short, warm note (1-2 sentences) from a {_practice_type_label(contact)} "
                f"professional to their client, {contact.household_name}, to go along with a gift{gift_desc}"
                f"{occasion}. {prompt_hint or ''}\n\n"
                "Respond with ONLY the note text -- no preamble, no quotation marks."
            )
            response = client.messages.create(
                model=MODEL,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _extract_text(response)
            if text:
                return text
        except Exception:
            pass  # fall through to the template below

    if event:
        base = f"Congratulations on your {event.display_label()}, {contact.household_name}!"
    else:
        base = f"Thinking of you, {contact.household_name}!"
    return f"{base} {prompt_hint}".strip() if prompt_hint else base


def generate_message(prompt_hint, contact, event):
    """Returns a short customer-facing message string for an email/text/
    handwritten_note action."""
    client = _client()
    if client is not None:
        try:
            prompt = (
                f"Write a short, warm message (2-3 sentences) from a {_practice_type_label(contact)} "
                f"professional to their client, {contact.household_name}, about their "
                f"{event.display_label()}. {prompt_hint or ''}\n\n"
                "Respond with ONLY the message text -- no preamble, no quotation marks."
            )
            response = client.messages.create(
                model=MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _extract_text(response)
            if text:
                return text
        except Exception:
            pass  # fall through to the template below

    base = f"Hi {contact.household_name}, thank you again for your business!"
    return f"{base} {prompt_hint}".strip() if prompt_hint else base
