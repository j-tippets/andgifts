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


def filter_facets(items):
    """The occasion/theme/price/lead-time ranges the catalog search-and-filter
    bar (see catalog/_macros.html) needs to build its controls --
    shared by the org-wide Gift Catalog page and the per-contact
    "Send a gift" browse page so both offer the same filtering instead
    of one silently having less than the other. Returns
    (all_occasions, all_themes, min_price, max_price, min_lead, max_lead), with
    the price/lead bounds collapsing to (0, 0) for an empty item list
    (the template hides those range inputs entirely when min == max)."""
    if items:
        price_dollars = [i.price_cents // 100 for i in items]
        lead_times = [i.lead_time_days for i in items]
        min_price, max_price = min(price_dollars), max(price_dollars)
        min_lead, max_lead = min(lead_times), max(lead_times)
    else:
        min_price = max_price = min_lead = max_lead = 0

    all_occasions = sorted({i.occasion for i in items if i.occasion}, key=str.lower)
    all_themes = sorted({tag for i in items for tag in i.tag_list()}, key=str.lower)
    return all_occasions, all_themes, min_price, max_price, min_lead, max_lead
