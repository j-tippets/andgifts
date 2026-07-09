from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import current_user

from app.extensions import db
from app.models import GiftCatalogItem, GiftTrigger
from app.decorators import admin_required
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str, tags_from_form

catalog_bp = Blueprint("catalog", __name__, url_prefix="/catalog")


@catalog_bp.route("/")
@admin_required
def list_catalog():
    org_items = (
        GiftCatalogItem.query.filter_by(org_id=current_user.org_id)
        .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
        .all()
    )
    return render_template("catalog/list.html", org_items=org_items)


@catalog_bp.route("/new", methods=["GET", "POST"])
@admin_required
def new_item():
    if request.method == "GET":
        return render_template("catalog/new.html")

    price_cents = dollars_to_cents(request.form.get("price"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return render_template("catalog/new.html")

    item = GiftCatalogItem(
        org_id=current_user.org_id,
        name=request.form["name"].strip(),
        description=request.form.get("description", "").strip() or None,
        price_cents=price_cents,
        item_type=request.form.get("item_type", "product"),
        interest_tags=tags_from_form(request.form.get("interest_tags")),
        image_url=request.form.get("image_url", "").strip() or None,
        is_active=True,
    )
    db.session.add(item)
    db.session.commit()
    flash(f"Added {item.name} to your catalog.", "success")
    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/<item_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_item(item_id):
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=current_user.org_id).first_or_404()

    if request.method == "GET":
        trigger_count = GiftTrigger.query.filter_by(suggested_gift_id=item.id).count()
        return render_template(
            "catalog/edit.html",
            item=item,
            price_display=cents_to_dollars_str(item.price_cents),
            trigger_count=trigger_count,
        )

    price_cents = dollars_to_cents(request.form.get("price"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return redirect(url_for("catalog.edit_item", item_id=item.id))

    item.name = request.form["name"].strip()
    item.description = request.form.get("description", "").strip() or None
    item.price_cents = price_cents
    item.item_type = request.form.get("item_type", item.item_type)
    item.interest_tags = tags_from_form(request.form.get("interest_tags"))
    item.image_url = request.form.get("image_url", "").strip() or None
    db.session.commit()
    flash(f"Updated {item.name}.", "success")
    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/<item_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_active(item_id):
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=current_user.org_id).first_or_404()
    item.is_active = not item.is_active
    db.session.commit()
    flash(f"{item.name} is now {'active' if item.is_active else 'inactive'}.", "success")
    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/<item_id>/delete", methods=["POST"])
@admin_required
def delete_item(item_id):
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=current_user.org_id).first_or_404()

    trigger_count = GiftTrigger.query.filter_by(suggested_gift_id=item.id).count()
    if trigger_count:
        flash(
            f"{item.name} is used by {trigger_count} campaign trigger{'s' if trigger_count != 1 else ''}. "
            "Deactivate it instead, or remove those triggers first.",
            "error",
        )
        return redirect(url_for("catalog.list_catalog"))

    name = item.name
    db.session.delete(item)
    db.session.commit()
    flash(f"Deleted {name} from the catalog.", "success")
    return redirect(url_for("catalog.list_catalog"))
