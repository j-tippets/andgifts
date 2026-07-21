def dollars_to_cents(raw):
    raw = (raw or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return round(float(raw) * 100)
    except ValueError:
        return None


def cents_to_dollars_str(cents):
    return f"{cents / 100:.2f}".rstrip("0").rstrip(".") if cents is not None else ""


def tags_from_form(raw):
    """Accept comma OR semicolon separated input, normalize to comma-separated."""
    raw = (raw or "").replace(";", ",")
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    return ", ".join(tags) if tags else None


def lead_time_from_form(raw, default=7):
    """Parse the lead-time-days field. Falls back to `default` for blank
    input; returns None for anything that isn't a positive whole number
    of days, so the caller can reject the submission."""
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        days = int(raw)
    except ValueError:
        return None
    return days if days > 0 else None
