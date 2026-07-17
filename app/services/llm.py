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
                "A real estate agent's client has these interests: "
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


def generate_gift_note(contact, event, gift_item, prompt_hint=None):
    """Returns a short note to go with a gift suggestion -- something the
    agent can attach to a physical gift or, later, send along with an
    e-gift-card delivery -- explaining what it's for. Same fallback
    contract as generate_message: real API call when a key is configured,
    a plain template otherwise."""
    client = _client()
    if client is not None:
        try:
            gift_desc = f" ({gift_item.name})" if gift_item else ""
            prompt = (
                "Write a short, warm note (1-2 sentences) from a real estate agent to "
                f"their client, {contact.household_name}, to go along with a gift{gift_desc} "
                f"for their {event.display_label()}. {prompt_hint or ''}\n\n"
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

    base = f"Congratulations on your {event.display_label()}, {contact.household_name}!"
    return f"{base} {prompt_hint}".strip() if prompt_hint else base


def generate_message(prompt_hint, contact, event):
    """Returns a short customer-facing message string for an email/text/
    handwritten_note action."""
    client = _client()
    if client is not None:
        try:
            prompt = (
                "Write a short, warm message (2-3 sentences) from a real estate agent "
                f"to their client, {contact.household_name}, about their "
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
