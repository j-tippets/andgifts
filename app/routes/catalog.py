from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import GiftCatalogItem, GiftTrigger
from app.decorators import admin_required

catalog_bp = Blueprint("catalog", __name__, url_prefix="/catalog")


def _dollars_to_cents(raw):
    raw = (raw or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return round(float(raw) * 100)
    except ValueError:
        return None


def _cents_to_dollars_str(cents):
    return f"{cents / 100:.2f}".rstrip("0").rstrip(".") if cents is not None else ""


def _tags_from_form(raw):
    """Accept comma OR semicolon separated input, normalize to comma-separated."""
    raw = (raw or "").replace(";", ",")
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    return ", ".join(tags) if tags else None


def _can_manage(item):
    if item.org_id is None:
        return current_user.platform_admin
    return current_user.is_admin and item.org_id == current_user.org_id


@catalog_bp.route("/")
@admin_required
def list_catalog():
    global_items = (
        GiftCatalogItem.query.filter_by(org_id=None)
        .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
        .all()
    )
    org_items = (
        GiftCatalogItem.query.filter_by(org_id=current_user.org_id)
        .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
        .all()
    )
    return render_template(
        "catalog/list.html",
        global_items=global_items,
        org_items=org_items,
    )


@catalog_bp.route("/new", methods=["GET", "POST"])
@admin_required
def new_item():
    if request.method == "GET":
        return render_template("catalog/new.html")

    is_global = current_user.platform_admin and request.form.get("scope") == "global"
    price_cents = _dollars_to_cents(request.form.get("price"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return render_template("catalog/new.html")

    item = GiftCatalogItem(
        org_id=None if is_global else current_user.org_id,
        name=request.form["name"].strip(),
        description=request.form.get("description", "").strip() or None,
        price_cents=price_cents,
        item_type=request.form.get("item_type", "product"),
        interest_tags=_tags_from_form(request.form.get("interest_tags")),
        image_url=request.form.get("image_url", "").strip() or None,
        is_active=True,
    )
    db.session.add(item)
    db.session.commit()
    flash(f"Added {item.name} to the {'global' if is_global else 'agency'} catalog.", "success")
    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/<item_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_item(item_id):
    item = GiftCatalogItem.query.get_or_404(item_id)
    if not _can_manage(item):
        flash("You don't have permission to edit that item.", "error")
        return redirect(url_for("catalog.list_catalog"))

    if request.method == "GET":
        trigger_count = GiftTrigger.query.filter_by(suggested_gift_id=item.id).count()
        return render_template(
            "catalog/edit.html",
            item=item,
            price_display=_cents_to_dollars_str(item.price_cents),
            trigger_count=trigger_count,
        )

    price_cents = _dollars_to_cents(request.form.get("price"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return redirect(url_for("catalog.edit_item", item_id=item.id))

    item.name = request.form["name"].strip()
    item.description = request.form.get("description", "").strip() or None
    item.price_cents = price_cents
    item.item_type = request.form.get("item_type", item.item_type)
    item.interest_tags = _tags_from_form(request.form.get("interest_tags"))
    item.image_url = request.form.get("image_url", "").strip() or None
    db.session.commit()
    flash(f"Updated {item.name}.", "success")
    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/<item_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_active(item_id):
    item = GiftCatalogItem.query.get_or_404(item_id)
    if not _can_manage(item):
        flash("You don't have permission to change that item.", "error")
        return redirect(url_for("catalog.list_catalog"))

    item.is_active = not item.is_active
    db.session.commit()
    flash(f"{item.name} is now {'active' if item.is_active else 'inactive'}.", "success")
    return redirect(url_for("catalog.list_catalog"))


@catalog_bp.route("/<item_id>/delete", methods=["POST"])
@admin_required
def delete_item(item_id):
    item = GiftCatalogItem.query.get_or_404(item_id)
    if not _can_manage(item):
        flash("You don't have permission to delete that item.", "error")
        return redirect(url_for("catalog.list_catalog"))

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
