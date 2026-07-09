from flask import Blueprint, render_template, redirect, url_for, request, flash

from app.extensions import db
from app.models import GiftCatalogItem, GiftTrigger, Org
from app.decorators import platform_admin_required
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str, tags_from_form

app_admin_bp = Blueprint("app_admin", __name__, url_prefix="/app-admin")


@app_admin_bp.route("/")
@platform_admin_required
def dashboard():
    return render_template(
        "app_admin/dashboard.html",
        global_catalog_count=GiftCatalogItem.query.filter_by(org_id=None).count(),
        org_count=Org.query.count(),
    )


# --- Global gift catalog -----------------------------------------------

@app_admin_bp.route("/catalog")
@platform_admin_required
def catalog_list():
    items = (
        GiftCatalogItem.query.filter_by(org_id=None)
        .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
        .all()
    )
    return render_template("app_admin/catalog_list.html", items=items)


@app_admin_bp.route("/catalog/new", methods=["GET", "POST"])
@platform_admin_required
def catalog_new():
    if request.method == "GET":
        return render_template("app_admin/catalog_new.html")

    price_cents = dollars_to_cents(request.form.get("price"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return render_template("app_admin/catalog_new.html")

    item = GiftCatalogItem(
        org_id=None,
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
    flash(f"Added {item.name} to the global catalog.", "success")
    return redirect(url_for("app_admin.catalog_list"))


@app_admin_bp.route("/catalog/<item_id>/edit", methods=["GET", "POST"])
@platform_admin_required
def catalog_edit(item_id):
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=None).first_or_404()

    if request.method == "GET":
        trigger_count = GiftTrigger.query.filter_by(suggested_gift_id=item.id).count()
        return render_template(
            "app_admin/catalog_edit.html",
            item=item,
            price_display=cents_to_dollars_str(item.price_cents),
            trigger_count=trigger_count,
        )

    price_cents = dollars_to_cents(request.form.get("price"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return redirect(url_for("app_admin.catalog_edit", item_id=item.id))

    item.name = request.form["name"].strip()
    item.description = request.form.get("description", "").strip() or None
    item.price_cents = price_cents
    item.item_type = request.form.get("item_type", item.item_type)
    item.interest_tags = tags_from_form(request.form.get("interest_tags"))
    item.image_url = request.form.get("image_url", "").strip() or None
    db.session.commit()
    flash(f"Updated {item.name}.", "success")
    return redirect(url_for("app_admin.catalog_list"))


@app_admin_bp.route("/catalog/<item_id>/toggle-active", methods=["POST"])
@platform_admin_required
def catalog_toggle_active(item_id):
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=None).first_or_404()
    item.is_active = not item.is_active
    db.session.commit()
    flash(f"{item.name} is now {'active' if item.is_active else 'inactive'}.", "success")
    return redirect(url_for("app_admin.catalog_list"))


@app_admin_bp.route("/catalog/<item_id>/delete", methods=["POST"])
@platform_admin_required
def catalog_delete(item_id):
    item = GiftCatalogItem.query.filter_by(id=item_id, org_id=None).first_or_404()

    trigger_count = GiftTrigger.query.filter_by(suggested_gift_id=item.id).count()
    if trigger_count:
        flash(
            f"{item.name} is used by {trigger_count} campaign trigger{'s' if trigger_count != 1 else ''}. "
            "Deactivate it instead, or remove those triggers first.",
            "error",
        )
        return redirect(url_for("app_admin.catalog_list"))

    name = item.name
    db.session.delete(item)
    db.session.commit()
    flash(f"Deleted {name} from the global catalog.", "success")
    return redirect(url_for("app_admin.catalog_list"))


# --- Orgs (placeholder: read-only overview for now) ---------------------

@app_admin_bp.route("/orgs")
@platform_admin_required
def orgs_list():
    orgs = Org.query.order_by(Org.created_at).all()
    return render_template("app_admin/orgs_list.html", orgs=orgs)


# --- Billing (placeholder) ----------------------------------------------

@app_admin_bp.route("/billing")
@platform_admin_required
def billing():
    return render_template("app_admin/billing.html")
