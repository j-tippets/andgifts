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


def stock_quantity_from_form(raw):
    """Parse the stock-on-hand admin field. Blank means "not tracked"
    (None -- the item stays always-orderable, same as every item today).
    Unlike lead_time_from_form, None is itself a valid parsed value here,
    so this returns (ok, value) instead of using None to mean "invalid" --
    ok=False for anything non-blank that isn't a whole number >= 0, so
    the caller can reject the submission the same way it does for price
    and lead time."""
    raw = (raw or "").strip()
    if not raw:
        return True, None
    try:
        qty = int(raw)
    except ValueError:
        return False, None
    return (True, qty) if qty >= 0 else (False, None)


def ai_search_matches(description, candidates, order_url_for):
    """Runs the free-form "explain the situation" gift search against
    `candidates` and shapes the result for either AI-search endpoint
    (per-contact in routes/contacts.py, org-wide in routes/catalog.py) --
    the only difference between the two is what `order_url_for(item)`
    points at (a specific contact's order form vs. pick-a-contact-first).
    Returns (used_ai, matches) where matches is a list of
    {"item_id", "name", "price_cents", "reasoning", "order_url"} dicts,
    ready to pass straight to jsonify()."""
    from app.services import llm

    matches, used_ai = llm.find_matching_gifts(description, candidates)
    return used_ai, [
        {
            "item_id": m["item"].id,
            "name": m["item"].name,
            "price_cents": m["item"].price_cents,
            "reasoning": m["reasoning"],
            "order_url": order_url_for(m["item"]),
        }
        for m in matches
    ]


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


def decrement_stock_on_payment(order):
    """Called from every place an Order transitions to status="paid" --
    routes.orders.confirm_order (synchronous card charge),
    routes.orders.stripe_webhook (async checkout.session.completed),
    and routes.dashboard.approve_action (automated flow/campaign gift
    approval) -- so stock moves at the same moment money does,
    regardless of which of those three paths the order came through.

    A no-op for an item with stock_quantity=None (not tracked) or an
    order with no gift_catalog_item at all (deleted since, or never
    set). Floors at 0 instead of going negative -- two orders racing
    past the last unit both still succeed at the payment layer (no
    reservation system yet), so this just stops the displayed count
    from reading -1, -2, etc.

    Does not commit -- the caller is expected to already be inside a
    single commit for the rest of the payment-success writes (ActionLog,
    ContactAuditLog, etc.), so this only touches the object in the
    session and lets that existing commit cover it too.
    """
    item = order.gift_catalog_item
    if item is None or item.stock_quantity is None:
        return
    item.stock_quantity = max(0, item.stock_quantity - 1)
