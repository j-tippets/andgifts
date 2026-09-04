from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db, limiter
from app.models import GiftCatalogItem, OrgCatalogSelection, Contact
from app.decorators import admin_required
from app.services.catalog_helpers import filter_facets, ai_search_matches
from app.routes.contacts import _search_contact_ids

catalog_bp = Blueprint("catalog", __name__, url_prefix="/catalog")

# How many contacts the "Who's this for?" picker shows before the agent
# needs to search -- an org with a big book of business shouldn't have
# to render every contact into the DOM up front.
PICK_CONTACT_LIMIT = 50


@catalog_bp.route("/")
@login_required
def list_catalog():
    org = current_user.org
    items = (
        GiftCatalogItem.query.filter_by(org_id=None, is_active=True)
        .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
        .all()
    )
    selected_ids = org.selected_item_ids() if org.catalog_curated else {i.id for i in items}
    all_occasions, all_themes, min_price, max_price, min_lead, max_lead = filter_facets(items)

    return render_template(
        "catalog/list.html",
        items=items,
        selected_ids=selected_ids,
        catalog_curated=org.catalog_curated,
        all_occasions=all_occasions,
        all_themes=all_themes,
        min_price=min_price,
        max_price=max_price,
        min_lead=min_lead,
        max_lead=max_lead,
    )


@catalog_bp.route("/ai-search", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def ai_search():
    """AJAX endpoint backing the "Ask AI for a gift idea" popup on the
    org-wide Gift Catalog page (see catalog/list.html + ai-gift-search.js)
    -- the org-wide counterpart to contacts.ai_search_gifts. Not tied to
    a specific contact yet, so candidates are this org's available+active
    catalog (the same set list_catalog already shows a regular agent as
    "included") and each match links to pick_contact_for_order instead
    of a specific contact's order form.
    """
    description = request.form.get("description", "").strip()
    if not description:
        return jsonify(error="Describe the situation first."), 400

    candidates = [i for i in current_user.org.available_catalog_items() if i.is_in_stock]
    used_ai, matches = ai_search_matches(
        description, candidates,
        order_url_for=lambda item: url_for("catalog.pick_contact_for_order", item_id=item.id),
    )
    return jsonify(used_ai=used_ai, matches=matches)


@catalog_bp.route("/<item_id>/order")
@login_required
def pick_contact_for_order(item_id):
    """Entry point from the org-wide Gift Catalog page: 'Order this gift'
    there doesn't already know which contact it's for, so pick one first,
    then hand off to the same per-contact order form used from a
    contact's own page.

    Do Not Contact contacts are excluded entirely -- see new_order /
    browse_gifts for the corresponding server-side hard block.

    Caps the initial render at PICK_CONTACT_LIMIT and lets
    contacts-search (AJAX) look up the rest by name as the agent types,
    rather than rendering every contact in the org up front.
    """
    item = GiftCatalogItem.query.filter_by(id=item_id, is_active=True).first_or_404()
    if not item.is_in_stock:
        flash(f"{item.name} is temporarily unavailable — it's out of stock.", "error")
        return redirect(url_for("catalog.list_catalog"))
    query = Contact.query.filter_by(org_id=current_user.org_id).filter(Contact.do_not_contact.is_(False))
    visible_query = Contact.visible_to(query, current_user).order_by(Contact.household_name)
    total_count = visible_query.count()
    contacts = visible_query.limit(PICK_CONTACT_LIMIT).all()
    return render_template(
        "orders/pick_contact.html",
        item=item,
        contacts=contacts,
        total_count=total_count,
        shown_limit=PICK_CONTACT_LIMIT,
    )


@catalog_bp.route("/<item_id>/order/contacts-search")
@login_required
def search_contacts_for_order(item_id):
    """AJAX backing the "Who's this for?" search box once the agent
    types past what pick_contact_for_order rendered up front -- looks
    across the whole org (not just the first page), same DNC exclusion
    and visibility rules as the initial render."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify(contacts=[])

    matching_ids = _search_contact_ids(q)
    query = Contact.query.filter(
        Contact.id.in_(matching_ids),
        Contact.org_id == current_user.org_id,
        Contact.do_not_contact.is_(False),
    )
    contacts = Contact.visible_to(query, current_user).order_by(Contact.household_name).limit(PICK_CONTACT_LIMIT).all()
    return jsonify(contacts=[
        {"id": c.id, "household_name": c.household_name}
        for c in contacts
    ])


@catalog_bp.route("/toggle/<item_id>", methods=["POST"])
@admin_required
def toggle_selection(item_id):
    org = current_user.org
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=None, is_active=True).first_or_404()

    if not org.catalog_curated:
        # Currently unrestricted ("all items"). The first exclusion switches
        # the org into curated mode: snapshot every currently-available item
        # except the one just being removed.
        current_ids = [i.id for i in org.available_catalog_items()]
        org.catalog_curated = True
        for iid in current_ids:
            if iid != item.id:
                db.session.add(OrgCatalogSelection(org_id=org.id, gift_catalog_item_id=iid))
        db.session.commit()
        flash(f"{item.name} removed. Your agency now uses a custom selection.", "success")
    else:
        existing = OrgCatalogSelection.query.filter_by(
            org_id=org.id, gift_catalog_item_id=item.id
        ).first()
        if existing:
            db.session.delete(existing)
            flash(f"{item.name} removed from your agency's catalog.", "success")
        else:
            db.session.add(OrgCatalogSelection(org_id=org.id, gift_catalog_item_id=item.id))
            flash(f"{item.name} added to your agency's catalog.", "success")
        db.session.commit()

    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/reset", methods=["POST"])
@admin_required
def reset_to_all():
    org = current_user.org
    OrgCatalogSelection.query.filter_by(org_id=org.id).delete()
    org.catalog_curated = False
    db.session.commit()
    flash("Your agency can now send any item from the global catalog again.", "success")
    return redirect(url_for("catalog.list_catalog"))
