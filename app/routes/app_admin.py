from flask import Blueprint, render_template, redirect, url_for, request, flash

from app.extensions import db
from app.models import GiftCatalogItem, GiftTrigger, Org, CampaignRecipe
from app.models.timeline import STANDARD_EVENT_TYPES
from app.decorators import platform_admin_required
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str, tags_from_form, lead_time_from_form

app_admin_bp = Blueprint("app_admin", __name__, url_prefix="/app-admin")


@app_admin_bp.route("/")
@platform_admin_required
def dashboard():
    return render_template(
        "app_admin/dashboard.html",
        global_catalog_count=GiftCatalogItem.query.filter_by(org_id=None).count(),
        org_count=Org.query.count(),
        recipe_count=CampaignRecipe.query.filter_by(is_active=True, org_id=None).count(),
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
    lead_time_days = lead_time_from_form(request.form.get("lead_time_days"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return render_template("app_admin/catalog_new.html")
    if lead_time_days is None:
        flash("Lead time must be a whole number of days greater than 0.", "error")
        return render_template("app_admin/catalog_new.html")

    item = GiftCatalogItem(
        org_id=None,
        name=request.form["name"].strip(),
        description=request.form.get("description", "").strip() or None,
        price_cents=price_cents,
        item_type=request.form.get("item_type", "product"),
        interest_tags=tags_from_form(request.form.get("interest_tags")),
        image_url=request.form.get("image_url", "").strip() or None,
        lead_time_days=lead_time_days,
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
    lead_time_days = lead_time_from_form(request.form.get("lead_time_days"))
    if not request.form.get("name", "").strip() or price_cents is None:
        flash("Name and a valid price are required.", "error")
        return redirect(url_for("app_admin.catalog_edit", item_id=item.id))
    if lead_time_days is None:
        flash("Lead time must be a whole number of days greater than 0.", "error")
        return redirect(url_for("app_admin.catalog_edit", item_id=item.id))

    item.name = request.form["name"].strip()
    item.description = request.form.get("description", "").strip() or None
    item.price_cents = price_cents
    item.item_type = request.form.get("item_type", item.item_type)
    item.interest_tags = tags_from_form(request.form.get("interest_tags"))
    item.image_url = request.form.get("image_url", "").strip() or None
    item.lead_time_days = lead_time_days
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


# --- Orgs -----------------------------------------------------------------

@app_admin_bp.route("/orgs")
@platform_admin_required
def orgs_list():
    orgs = Org.query.order_by(Org.created_at).all()
    return render_template("app_admin/orgs_list.html", orgs=orgs)


@app_admin_bp.route("/orgs/<org_id>/edit", methods=["GET", "POST"])
@platform_admin_required
def org_edit(org_id):
    org = Org.query.get_or_404(org_id)

    if request.method == "POST":
        org.office_address = request.form.get("office_address", "").strip() or None
        # Checkbox only appears in form data when checked.
        org.dropoff_enabled = request.form.get("dropoff_enabled") == "on"

        if org.dropoff_enabled and org.tier not in ("pro", "team"):
            flash(
                f"Saved, but drop-off won't actually show at checkout until {org.name} is on "
                "the pro or team plan.",
                "warning",
            )
        else:
            flash(f"Updated {org.name}.", "success")

        db.session.commit()
        return redirect(url_for("app_admin.org_edit", org_id=org.id))

    return render_template("app_admin/org_edit.html", org=org)


# --- Billing (placeholder) ----------------------------------------------

@app_admin_bp.route("/billing")
@platform_admin_required
def billing():
    return render_template("app_admin/billing.html")


# --- Campaign recipe book ------------------------------------------------

def _recipe_form_kwargs():
    """Shared dropdown data for the recipe new/edit forms."""
    return dict(
        event_types=STANDARD_EVENT_TYPES,
        gift_items=(
            GiftCatalogItem.query.filter_by(org_id=None, is_active=True)
            .order_by(GiftCatalogItem.price_cents, GiftCatalogItem.name)
            .all()
        ),
    )


def _save_recipe_from_form(recipe):
    recipe.name = request.form["name"].strip()
    recipe.description = request.form.get("description", "").strip() or None
    recipe.event_type = request.form["event_type"]

    recipe.timing_direction = request.form.get("timing_direction", "after")
    try:
        recipe.timing_amount = max(0, int(request.form.get("timing_amount", "1")))
    except ValueError:
        recipe.timing_amount = 1
    recipe.timing_unit = request.form.get("timing_unit", "day")
    recipe.repeat_enabled = bool(request.form.get("repeat_enabled"))

    price_max = dollars_to_cents(request.form.get("price_max"))
    recipe.price_max_cents = price_max
    recipe.use_llm_gift_selection = bool(request.form.get("use_llm_gift_selection"))

    recipe.action_type = request.form["action_type"]
    gift_id = request.form.get("suggested_gift_id", "").strip()
    recipe.suggested_gift_id = gift_id or None

    recipe.use_llm_copy = bool(request.form.get("use_llm_copy"))
    recipe.message_template = request.form.get("message_template", "").strip() or None
    recipe.llm_prompt_hint = request.form.get("llm_prompt_hint", "").strip() or None


@app_admin_bp.route("/recipes")
@platform_admin_required
def recipe_list():
    """Only global (platform-authored) flows -- each agency manages its
    own local flows from within its own Flow Library instead."""
    recipes = CampaignRecipe.query.filter_by(org_id=None).order_by(CampaignRecipe.name).all()
    return render_template("app_admin/recipe_list.html", recipes=recipes)


@app_admin_bp.route("/recipes/new", methods=["GET", "POST"])
@platform_admin_required
def recipe_new():
    if request.method == "GET":
        return render_template("app_admin/recipe_new.html", **_recipe_form_kwargs())

    if not request.form.get("name", "").strip() or not request.form.get("event_type"):
        flash("Name and a trigger event are required.", "error")
        return render_template("app_admin/recipe_new.html", **_recipe_form_kwargs())

    recipe = CampaignRecipe(is_active=True, org_id=None)
    _save_recipe_from_form(recipe)
    db.session.add(recipe)
    db.session.commit()
    flash(f"Added \u201c{recipe.name}\u201d to the flow library.", "success")
    return redirect(url_for("app_admin.recipe_list"))


@app_admin_bp.route("/recipes/<recipe_id>/edit", methods=["GET", "POST"])
@platform_admin_required
def recipe_edit(recipe_id):
    recipe = CampaignRecipe.query.filter_by(id=recipe_id, org_id=None).first_or_404()

    if request.method == "GET":
        return render_template(
            "app_admin/recipe_edit.html",
            recipe=recipe,
            price_max_display=cents_to_dollars_str(recipe.price_max_cents),
            **_recipe_form_kwargs(),
        )

    if not request.form.get("name", "").strip() or not request.form.get("event_type"):
        flash("Name and a trigger event are required.", "error")
        return redirect(url_for("app_admin.recipe_edit", recipe_id=recipe.id))

    _save_recipe_from_form(recipe)
    db.session.commit()
    flash(f"Updated \u201c{recipe.name}\u201d.", "success")
    return redirect(url_for("app_admin.recipe_list"))


@app_admin_bp.route("/recipes/<recipe_id>/toggle-active", methods=["POST"])
@platform_admin_required
def recipe_toggle_active(recipe_id):
    recipe = CampaignRecipe.query.filter_by(id=recipe_id, org_id=None).first_or_404()
    recipe.is_active = not recipe.is_active
    db.session.commit()
    flash(f"\u201c{recipe.name}\u201d is now {'active' if recipe.is_active else 'inactive'}.", "success")
    return redirect(url_for("app_admin.recipe_list"))
