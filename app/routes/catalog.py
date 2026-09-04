from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db, limiter
from app.models import GiftCatalogItem, OrgCatalogSelection, Contact
from app.decorators import admin_required
from app.services.catalog_helpers import filter_facets, ai_search_matches

catalog_bp = Blueprint("catalog", __name__, url_prefix="/catalog")


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

    candidates = current_user.org.available_catalog_items()
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
    contact's own page."""
    item = GiftCatalogItem.query.filter_by(id=item_id, is_active=True).first_or_404()
    query = Contact.query.filter_by(org_id=current_user.org_id).filter(Contact.do_not_contact.is_(False))
    contacts = Contact.visible_to(query, current_user).order_by(Contact.household_name).all()
    return render_template("orders/pick_contact.html", item=item, contacts=contacts)


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
