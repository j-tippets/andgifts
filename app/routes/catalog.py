from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import current_user

from app.extensions import db
from app.models import GiftCatalogItem, OrgCatalogSelection
from app.decorators import admin_required

catalog_bp = Blueprint("catalog", __name__, url_prefix="/catalog")


@catalog_bp.route("/")
@admin_required
def list_catalog():
    org = current_user.org
    items = (
        GiftCatalogItem.query.filter_by(org_id=None, is_active=True)
        .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
        .all()
    )
    selected_ids = org.selected_item_ids() if org.catalog_curated else {i.id for i in items}
    return render_template(
        "catalog/list.html",
        items=items,
        selected_ids=selected_ids,
        catalog_curated=org.catalog_curated,
    )


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
