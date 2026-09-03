from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app

from app.extensions import db
from app.models import GiftCatalogItem, GiftTrigger, Org, CampaignRecipe, Badge, OrgEventLog, PracticeType, PracticeTypeMilestone
from app.models.timeline import slugify_event_key
from app.decorators import platform_admin_required
from app.services.catalog_helpers import dollars_to_cents, cents_to_dollars_str, tags_from_form, lead_time_from_form
from app.services.practice_types import seed_org_milestones

app_admin_bp = Blueprint("app_admin", __name__, url_prefix="/app-admin")


@app_admin_bp.route("/")
@platform_admin_required
def dashboard():
    return render_template(
        "app_admin/dashboard.html",
        global_catalog_count=GiftCatalogItem.query.filter_by(org_id=None).count(),
        org_count=Org.query.count(),
        recipe_count=CampaignRecipe.query.filter_by(is_active=True, org_id=None).count(),
        badge_count=Badge.query.filter_by(scope="global").count(),
        recent_event_count=OrgEventLog.query.count(),
        practice_type_count=PracticeType.query.count(),
    )


PER_PAGE_OPTIONS = (10, 25, 50, 100)


@app_admin_bp.route("/activity")
@platform_admin_required
def activity_list():
    """Signup/upgrade/downgrade history across every org -- the record-
    of-truth view to complement the one-off notification emails (see
    services/org_events.record_org_event), useful for spotting usage
    patterns over time rather than just reacting to individual pings.

    Filtered/paginated server-side (not loaded-then-filtered-in-JS like
    catalog/list.html) since this table only grows over time and could
    get large -- unlike the gift catalog, which is bounded by however
    many items Jeremiah has actually added."""
    from datetime import datetime, timedelta

    q = request.args.get("q", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    event_type = request.args.get("event_type", "").strip()

    try:
        per_page = int(request.args.get("per_page", 10))
    except ValueError:
        per_page = 10
    if per_page not in PER_PAGE_OPTIONS:
        per_page = 10

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    query = OrgEventLog.query

    if q:
        query = query.filter(OrgEventLog.org_name_snapshot.ilike(f"%{q}%"))

    if event_type in ("signup", "upgrade", "downgrade"):
        query = query.filter(OrgEventLog.event_type == event_type)

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(OrgEventLog.created_at >= start_dt)
        except ValueError:
            start_date = ""

    if end_date:
        try:
            # Inclusive of the whole end day -- created_at has a time
            # component, so a bare "< end_date" would cut off that day
            # at midnight and silently drop everything that happened on
            # the end date itself.
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(OrgEventLog.created_at < end_dt)
        except ValueError:
            end_date = ""

    pagination = query.order_by(OrgEventLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        "app_admin/activity.html",
        events=pagination.items,
        pagination=pagination,
        pricing_display=current_app.config["PRICING_DISPLAY"],
        per_page_options=PER_PAGE_OPTIONS,
        per_page=per_page,
        q=q,
        start_date=start_date,
        end_date=end_date,
        event_type=event_type,
    )


# --- Practice types (milestone presets) ---------------------------------

@app_admin_bp.route("/practice-types")
@platform_admin_required
def practice_type_list():
    types = PracticeType.query.order_by(PracticeType.name).all()
    org_counts = {pt.id: Org.query.filter_by(practice_type_id=pt.id).count() for pt in types}
    return render_template("app_admin/practice_type_list.html", types=types, org_counts=org_counts)


@app_admin_bp.route("/practice-types/new", methods=["GET", "POST"])
@platform_admin_required
def practice_type_new():
    if request.method == "GET":
        return render_template("app_admin/practice_type_new.html")

    name = request.form.get("name", "").strip()
    if not name:
        flash("Give the practice type a name.", "error")
        return render_template("app_admin/practice_type_new.html")

    key = slugify_event_key(name)
    if PracticeType.query.filter_by(key=key).first():
        flash(f"A practice type already exists with a name too similar to '{name}'.", "error")
        return render_template("app_admin/practice_type_new.html")

    practice_type = PracticeType(key=key, name=name)
    db.session.add(practice_type)
    db.session.commit()
    flash(f"Added '{name}'. Now add its starting milestones.", "success")
    return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type.id))


@app_admin_bp.route("/practice-types/<practice_type_id>/edit", methods=["GET", "POST"])
@platform_admin_required
def practice_type_edit(practice_type_id):
    practice_type = PracticeType.query.get_or_404(practice_type_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name can't be blank.", "error")
        else:
            practice_type.name = name
            db.session.commit()
            flash(f"Updated '{name}'.", "success")
        return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type.id))

    org_count = Org.query.filter_by(practice_type_id=practice_type.id).count()
    return render_template(
        "app_admin/practice_type_edit.html",
        practice_type=practice_type,
        org_count=org_count,
    )


@app_admin_bp.route("/practice-types/<practice_type_id>/milestones/new", methods=["POST"])
@platform_admin_required
def practice_type_milestone_new(practice_type_id):
    """Adding a milestone here only affects orgs seeded AFTER this save
    -- see services.practice_types.seed_org_milestones. Orgs already on
    this practice type keep whatever they have; they don't retroactively
    pick up new preset milestones."""
    practice_type = PracticeType.query.get_or_404(practice_type_id)

    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the milestone a name.", "error")
        return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type.id))

    key = slugify_event_key(label)
    if key == "custom" or PracticeTypeMilestone.query.filter_by(
        practice_type_id=practice_type.id, key=key
    ).first():
        flash(f"A milestone already exists with a name too similar to '{label}'.", "error")
        return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type.id))

    next_order = db.session.query(db.func.coalesce(db.func.max(PracticeTypeMilestone.sort_order), -1)).filter_by(
        practice_type_id=practice_type.id
    ).scalar() + 1

    db.session.add(PracticeTypeMilestone(
        practice_type_id=practice_type.id, key=key, label=label, sort_order=next_order,
    ))
    db.session.commit()
    flash(f"Added '{label}'.", "success")
    return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type.id))


@app_admin_bp.route("/practice-types/<practice_type_id>/milestones/<milestone_id>/delete", methods=["POST"])
@platform_admin_required
def practice_type_milestone_delete(practice_type_id, milestone_id):
    milestone = PracticeTypeMilestone.query.filter_by(
        id=milestone_id, practice_type_id=practice_type_id
    ).first_or_404()
    label = milestone.label
    db.session.delete(milestone)
    db.session.commit()
    flash(
        f"Removed '{label}' from the preset. Orgs that already copied it keep it -- "
        "this only changes what new orgs on this practice type start with.",
        "success",
    )
    return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type_id))


@app_admin_bp.route("/practice-types/<practice_type_id>/milestones/<milestone_id>/move", methods=["POST"])
@platform_admin_required
def practice_type_milestone_move(practice_type_id, milestone_id):
    """Swaps this milestone's sort_order with its neighbor in the given
    direction -- simple adjacent-swap reordering, same idea as the
    up/down buttons on the agent priority list, just without drag-and-
    drop since this is a low-traffic admin-only page."""
    direction = request.form.get("direction")
    milestones = (
        PracticeTypeMilestone.query.filter_by(practice_type_id=practice_type_id)
        .order_by(PracticeTypeMilestone.sort_order).all()
    )
    ids = [m.id for m in milestones]
    if milestone_id not in ids:
        return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type_id))

    i = ids.index(milestone_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(milestones):
        milestones[i].sort_order, milestones[j].sort_order = milestones[j].sort_order, milestones[i].sort_order
        db.session.commit()
    return redirect(url_for("app_admin.practice_type_edit", practice_type_id=practice_type_id))


@app_admin_bp.route("/practice-types/<practice_type_id>/delete", methods=["POST"])
@platform_admin_required
def practice_type_delete(practice_type_id):
    practice_type = PracticeType.query.get_or_404(practice_type_id)
    org_count = Org.query.filter_by(practice_type_id=practice_type.id).count()
    if org_count:
        flash(
            f"Can't delete '{practice_type.name}' -- {org_count} org{'s' if org_count != 1 else ''} "
            "still assigned to it. Reassign them first.",
            "error",
        )
        return redirect(url_for("app_admin.practice_type_list"))

    name = practice_type.name
    db.session.delete(practice_type)
    db.session.commit()
    flash(f"Deleted '{name}'.", "success")
    return redirect(url_for("app_admin.practice_type_list"))


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
        sku=request.form.get("sku", "").strip() or None,
        occasion=request.form.get("occasion", "").strip() or None,
        recipe_id=request.form.get("recipe_id", "").strip() or None,
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
    item.sku = request.form.get("sku", "").strip() or None
    item.occasion = request.form.get("occasion", "").strip() or None
    item.recipe_id = request.form.get("recipe_id", "").strip() or None
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

        new_practice_type_id = request.form.get("practice_type_id") or None
        seeded = []
        if new_practice_type_id != org.practice_type_id:
            org.practice_type_id = new_practice_type_id
            db.session.flush()  # so org.practice_type is loadable for seeding below
            if new_practice_type_id:
                seeded = seed_org_milestones(org)

        if org.dropoff_enabled and org.tier not in ("pro", "team"):
            flash(
                f"Saved, but drop-off won't actually show at checkout until {org.name} is on "
                "the pro or team plan.",
                "warning",
            )
        elif seeded:
            flash(
                f"Updated {org.name}. Added {len(seeded)} milestone{'s' if len(seeded) != 1 else ''} "
                "from the new practice type -- anything they already had (built-in or personalized) was left alone.",
                "success",
            )
        else:
            flash(f"Updated {org.name}.", "success")

        db.session.commit()
        return redirect(url_for("app_admin.org_edit", org_id=org.id))

    events = OrgEventLog.query.filter_by(org_id=org.id).order_by(OrgEventLog.created_at.desc()).all()
    return render_template(
        "app_admin/org_edit.html",
        org=org,
        events=events,
        practice_types=PracticeType.query.order_by(PracticeType.name).all(),
        pricing_display=current_app.config["PRICING_DISPLAY"],
    )


# --- Billing (placeholder) ----------------------------------------------

@app_admin_bp.route("/billing")
@platform_admin_required
def billing():
    return render_template("app_admin/billing.html")


# --- Global badges ---------------------------------------------------------

@app_admin_bp.route("/badges")
@platform_admin_required
def badge_list():
    badges = Badge.query.filter_by(scope="global").order_by(Badge.label).all()
    return render_template("app_admin/badges.html", badges=badges)


@app_admin_bp.route("/badges/new", methods=["POST"])
@platform_admin_required
def badge_new():
    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the badge a name.", "error")
        return redirect(url_for("app_admin.badge_list"))

    badge = Badge(
        scope="global",
        org_id=None,
        owner_user_id=None,
        label=label,
        color=request.form.get("color", "").strip() or None,
    )
    db.session.add(badge)
    db.session.commit()
    flash(f"Added the global '{badge.label}' badge -- every agency can now use it.", "success")
    return redirect(url_for("app_admin.badge_list"))


@app_admin_bp.route("/badges/<badge_id>/edit", methods=["POST"])
@platform_admin_required
def badge_edit(badge_id):
    badge = Badge.query.filter_by(id=badge_id, scope="global").first_or_404()

    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the badge a name.", "error")
        return redirect(url_for("app_admin.badge_list"))

    badge.label = label
    badge.color = request.form.get("color", "").strip() or None
    db.session.commit()
    flash(f"Updated the global '{badge.label}' badge. Every contact that already had it keeps it.", "success")
    return redirect(url_for("app_admin.badge_list"))


@app_admin_bp.route("/badges/<badge_id>/delete", methods=["POST"])
@platform_admin_required
def badge_delete(badge_id):
    badge = Badge.query.filter_by(id=badge_id, scope="global").first_or_404()
    label = badge.label
    db.session.delete(badge)
    db.session.commit()
    flash(f"Removed the global '{label}' badge from every agency's contacts.", "success")
    return redirect(url_for("app_admin.badge_list"))


# --- Campaign recipe book ------------------------------------------------

def _recipe_form_kwargs():
    """Shared dropdown data for the recipe new/edit forms. A global
    recipe isn't scoped to one org, so its trigger options are every
    milestone key defined across every practice type's preset --
    deduped by key (first-seen label wins in the rare case two presets
    reuse the same key with different labels)."""
    seen = []
    for pm in PracticeTypeMilestone.query.order_by(PracticeTypeMilestone.key).all():
        if pm.key not in seen:
            seen.append(pm.key)
    return dict(
        event_types=seen,
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
    try:
        recipe.recur_interval_amount = max(1, int(request.form.get("recur_interval_amount", "1")))
    except ValueError:
        recipe.recur_interval_amount = 1
    recipe.recur_interval_unit = request.form.get("recur_interval_unit", "year")

    max_occurrences_raw = request.form.get("max_occurrences", "").strip()
    if max_occurrences_raw:
        try:
            recipe.max_occurrences = max(1, int(max_occurrences_raw))
        except ValueError:
            recipe.max_occurrences = None
    else:
        recipe.max_occurrences = None

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
