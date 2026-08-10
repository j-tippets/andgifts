from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_
from app.extensions import db
from app.models import (
    Contact, ContactPerson, ContactMethod,
    TimelineEvent, STANDARD_EVENT_TYPES, CustomEventType, slugify_event_key, MilestonePriority,
    CustomFieldDefinition, CustomFieldValue, CUSTOM_FIELD_TYPES,
    SuggestedAction, ActionLog, User, ContactAuditLog,
    GiftCatalogItem, Order, Badge,
)
from app.decorators import admin_required
from app.services.stripe_client import get_stripe
from app.services.storage import upload_contact_photo, delete_contact_photo, StorageError

contacts_bp = Blueprint("contacts", __name__, url_prefix="/contacts")


@contacts_bp.route("/")
@login_required
def list_contacts():
    status_filter = request.args.get("status")
    search_term = request.args.get("q", "").strip()

    query = Contact.query.filter_by(org_id=current_user.org_id)
    query = Contact.visible_to(query, current_user)
    if status_filter in ("new", "active", "past"):
        query = query.filter_by(status=status_filter)

    if search_term:
        query = query.filter(Contact.id.in_(_search_contact_ids(search_term)))

    contacts = query.order_by(Contact.household_name).all()
    return render_template(
        "contacts/list.html",
        contacts=contacts,
        status_filter=status_filter,
        search_term=search_term,
    )


def _search_contact_ids(search_term):
    """
    Contact ids (within the current org) whose household name, household
    notes, any person's name, any contact method (email/phone), or any
    custom field value visible to the current user matches the search
    term. Callers are expected to further scope the result through
    Contact.visible_to.
    """
    like = f"%{search_term}%"
    matching_ids = set()

    name_matches = Contact.query.filter(
        Contact.org_id == current_user.org_id,
        (Contact.household_name.ilike(like)) | (Contact.notes.ilike(like)),
    )
    matching_ids.update(c.id for c in name_matches.all())

    person_matches = (
        db.session.query(ContactPerson.contact_id)
        .join(Contact, Contact.id == ContactPerson.contact_id)
        .filter(Contact.org_id == current_user.org_id)
        .filter((ContactPerson.first_name.ilike(like)) | (ContactPerson.last_name.ilike(like)))
    )
    matching_ids.update(row[0] for row in person_matches.all())

    method_matches = (
        db.session.query(ContactPerson.contact_id)
        .join(ContactMethod, ContactMethod.person_id == ContactPerson.id)
        .join(Contact, Contact.id == ContactPerson.contact_id)
        .filter(Contact.org_id == current_user.org_id)
        .filter(ContactMethod.value.ilike(like))
    )
    matching_ids.update(row[0] for row in method_matches.all())

    visible_field_ids = [f.id for f in _visible_custom_fields()]
    if visible_field_ids:
        field_value_matches = (
            db.session.query(CustomFieldValue.contact_id)
            .filter(CustomFieldValue.field_definition_id.in_(visible_field_ids))
            .filter(CustomFieldValue.value.ilike(like))
        )
        matching_ids.update(row[0] for row in field_value_matches.all())

    return matching_ids


@contacts_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_contact():
    org = current_user.org

    if request.method == "GET":
        if not org.can_add_contact():
            flash(
                f"You've hit your plan's contact limit "
                f"({org.limit_for('contacts')}). Upgrade to add more.",
                "error",
            )
            return redirect(url_for("contacts.list_contacts"))
        return render_template(
            "contacts/new.html",
            custom_fields=_visible_custom_fields(),
            custom_values={},
        )

    if not org.can_add_contact():
        flash("Contact limit reached for your plan.", "error")
        return redirect(url_for("contacts.list_contacts"))

    contact = Contact(
        org_id=org.id,
        household_name=request.form["household_name"],
        status=request.form.get("status", "new"),
        notes=request.form.get("notes", "").strip() or None,
        owner_user_id=current_user.id if request.form.get("keep_private") else None,
    )
    db.session.add(contact)
    db.session.flush()

    photo = request.files.get("photo")
    if photo and photo.filename:
        try:
            contact.photo_url = upload_contact_photo(photo, contact.id)
        except StorageError as exc:
            flash(str(exc), "error")
            return redirect(url_for("contacts.new_contact"))

    # Head of household (required)
    head = ContactPerson(
        contact_id=contact.id,
        first_name=request.form["head_first_name"],
        last_name=request.form["head_last_name"],
        household_role="head",
    )
    db.session.add(head)
    db.session.flush()
    _add_contact_methods(head.id, request.form, prefix="head")

    # Spouse (optional)
    if request.form.get("spouse_first_name"):
        spouse = ContactPerson(
            contact_id=contact.id,
            first_name=request.form["spouse_first_name"],
            last_name=request.form.get("spouse_last_name", request.form["head_last_name"]),
            household_role="spouse",
        )
        db.session.add(spouse)
        db.session.flush()
        _add_contact_methods(spouse.id, request.form, prefix="spouse")

    _save_custom_field_values(contact, request.form, _visible_custom_fields())

    _log_contact_activity(contact, "created", f"Created by {current_user.full_name}.")

    # Seed first_contact timeline event automatically
    db.session.add(TimelineEvent(
        contact_id=contact.id,
        event_type="first_contact",
        event_date=datetime.utcnow().date(),
        is_recurring=False,
    ))

    db.session.commit()
    flash(f"Added {contact.household_name}.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))


def _log_contact_activity(contact, action, summary):
    db.session.add(ContactAuditLog(
        org_id=contact.org_id,
        contact_id=contact.id,
        contact_name_snapshot=contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action=action,
        summary=summary,
    ))


def _add_contact_methods(person_id, form, prefix):
    email = form.get(f"{prefix}_email")
    phone = form.get(f"{prefix}_phone")
    if email:
        db.session.add(ContactMethod(person_id=person_id, method_type="email", subtype="personal", value=email, is_primary=True))
    if phone:
        db.session.add(ContactMethod(person_id=person_id, method_type="phone", subtype="mobile", value=phone, is_primary=True))


def _sync_contact_method(person_id, method_type, subtype, value):
    """Create/update/remove a single ContactMethod row to match a submitted value."""
    existing = ContactMethod.query.filter_by(person_id=person_id, method_type=method_type).first()
    if value:
        if existing:
            existing.value = value
            existing.subtype = subtype
        else:
            db.session.add(ContactMethod(
                person_id=person_id, method_type=method_type, subtype=subtype,
                value=value, is_primary=True,
            ))
    elif existing:
        db.session.delete(existing)


def _visible_custom_fields():
    """Org-wide custom fields plus the current agent's own personal fields."""
    query = CustomFieldDefinition.query.filter_by(org_id=current_user.org_id)
    return CustomFieldDefinition.visible_to(query, current_user).order_by(
        CustomFieldDefinition.scope, CustomFieldDefinition.label
    ).all()


def _visible_event_types():
    """(key, label) pairs for the timeline event-type dropdown: the
    built-in milestones first, then this org's custom ones the current
    agent can see (org-wide, plus their own personal milestones), then
    the 'Custom' escape hatch last for a genuine one-off label."""
    standard = [(t, t.replace("_", " ").title()) for t in STANDARD_EVENT_TYPES if t != "custom"]
    query = CustomEventType.query.filter_by(org_id=current_user.org_id)
    custom = CustomEventType.visible_to(query, current_user).order_by(CustomEventType.label).all()
    return standard + [(c.key, c.label) for c in custom] + [("custom", "Custom")]


def _visible_badges():
    """Every global badge plus the current agent's own personal ones --
    same shape as _visible_custom_fields above."""
    return Badge.visible_to(Badge.query, current_user).order_by(Badge.scope, Badge.label).all()


def _save_contact_badges(contact, form, badges):
    selected_ids = set(form.getlist("badge_ids"))
    contact.badges = [b for b in badges if b.id in selected_ids]


def _save_custom_field_values(contact, form, fields):
    existing = {v.field_definition_id: v for v in contact.custom_values}
    for field in fields:
        raw_value = (
            ("1" if form.get(f"custom_{field.id}") else "0")
            if field.field_type == "checkbox"
            else form.get(f"custom_{field.id}", "").strip()
        )
        value_row = existing.get(field.id)
        if raw_value:
            if value_row:
                value_row.value = raw_value
            else:
                db.session.add(CustomFieldValue(
                    contact_id=contact.id, field_definition_id=field.id, value=raw_value
                ))
        elif value_row:
            db.session.delete(value_row)


@contacts_bp.route("/<contact_id>/badges", methods=["POST"])
@login_required
def update_contact_badges(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    badges = _visible_badges()
    _save_contact_badges(contact, request.form, badges)
    db.session.commit()
    flash("Updated badges.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))


@contacts_bp.route("/<contact_id>")
@login_required
def view_contact(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    custom_values = {v.field_definition_id: v.value for v in contact.custom_values}
    pending_actions = (
        SuggestedAction.query
        .filter_by(contact_id=contact.id, status="pending")
        .order_by(SuggestedAction.target_date)
        .all()
    )
    recent_activity = (
        ContactAuditLog.query.filter_by(contact_id=contact.id)
        .order_by(ContactAuditLog.created_at.desc())
        .limit(15)
        .all()
    )

    # Badge data for the Timeline: for each event, the most recent completed
    # action that was triggered by it (if any), so the template can show a
    # small "done" indicator without agents having to open Recent activity.
    # "Completed" means an ActionLog row exists and, for channels that track
    # real delivery (currently just email), that delivery actually succeeded
    # -- delivery_status is NULL for channels that aren't wired to a real
    # send yet (gift, text, handwritten_note), which still counts as done.
    completed_by_event_id = {}
    logs = (
        ActionLog.query
        .join(SuggestedAction, ActionLog.suggested_action_id == SuggestedAction.id)
        .filter(
            SuggestedAction.contact_id == contact.id,
            SuggestedAction.triggering_event_id.isnot(None),
            or_(ActionLog.delivery_status.is_(None), ActionLog.delivery_status != "failed"),
        )
        .all()
    )
    for log in sorted(logs, key=lambda l: l.sent_at):
        event_id = log.suggested_action.triggering_event_id
        completed_by_event_id[event_id] = log  # last write wins -- keeps most recent

    return render_template(
        "contacts/view.html",
        contact=contact,
        event_types=_visible_event_types(),
        custom_fields=_visible_custom_fields(),
        custom_values=custom_values,
        badges=_visible_badges(),
        contact_badge_ids={b.id for b in contact.badges},
        pending_actions=pending_actions,
        recent_activity=recent_activity,
        completed_by_event_id=completed_by_event_id,
    )


@contacts_bp.route("/<contact_id>/preferences", methods=["POST"])
@login_required
def update_contact_preferences(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()

    old_do_not_contact = contact.do_not_contact
    old_marketing_opt_out = contact.marketing_opt_out

    contact.marketing_opt_out = bool(request.form.get("marketing_opt_out"))
    contact.do_not_contact = bool(request.form.get("do_not_contact"))

    changes = []
    if old_marketing_opt_out != contact.marketing_opt_out:
        changes.append(
            "Opted out of marketing." if contact.marketing_opt_out else "Opted back into marketing."
        )
    if old_do_not_contact != contact.do_not_contact:
        if contact.do_not_contact:
            cancelled = (
                SuggestedAction.query
                .filter_by(contact_id=contact.id, status="pending")
                .all()
            )
            for suggestion in cancelled:
                suggestion.status = "skipped"
                suggestion.resolved_at = datetime.utcnow()
            changes.append(
                f"Marked Do Not Contact. Cancelled {len(cancelled)} pending suggestion{'s' if len(cancelled) != 1 else ''}."
                if cancelled else "Marked Do Not Contact."
            )
        else:
            changes.append("Removed Do Not Contact.")

    if changes:
        _log_contact_activity(contact, "updated", " ".join(changes))
        db.session.commit()
        flash(" ".join(changes), "success")
    else:
        db.session.rollback()

    return redirect(url_for("contacts.view_contact", contact_id=contact.id))


@contacts_bp.route("/<contact_id>/gifts")
@login_required
def browse_gifts(contact_id):
    """Catalog browse scoped to a single contact, for placing a one-off
    gift order right now instead of waiting on the automated suggestion
    engine."""
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    items = current_user.org.available_catalog_items()
    return render_template("orders/browse.html", contact=contact, items=items)


@contacts_bp.route("/<contact_id>/order/<item_id>", methods=["GET", "POST"])
@login_required
def new_order(contact_id, item_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    item = GiftCatalogItem.query.filter_by(id=item_id, is_active=True).first_or_404()
    flat_rate = current_app.config.get("FLAT_RATE_SHIPPING_CENTS", 595)

    if request.method == "POST":
        fulfillment_method = request.form.get("fulfillment_method")
        pickup_location = current_app.config.get("PICKUP_LOCATION_ADDRESS")
        valid_methods = ["shipping", "pickup"]
        if contact.org.can_offer_dropoff():
            valid_methods.append("dropoff")
        # Re-check eligibility server-side rather than trusting the submitted
        # value -- an org that had dropoff toggled off (or dropped out of
        # pro tier) between page load and submit shouldn't be able to sneak
        # a free drop-off through by resubmitting a stale form.
        if fulfillment_method not in valid_methods:
            flash("Choose a valid delivery option.", "error")
            return redirect(url_for("contacts.new_order", contact_id=contact.id, item_id=item.id))

        shipping_cost_cents = flat_rate if fulfillment_method == "shipping" else 0

        order = Order(
            org_id=current_user.org_id,
            contact_id=contact.id,
            ordered_by_user_id=current_user.id,
            gift_catalog_item_id=item.id,
            gift_name_snapshot=item.name,
            gift_price_cents=item.price_cents,
            fulfillment_method=fulfillment_method,
            pickup_location=pickup_location if fulfillment_method == "pickup" else None,
            dropoff_location=contact.org.office_address if fulfillment_method == "dropoff" else None,
            shipping_cost_cents=shipping_cost_cents,
        )
        db.session.add(order)
        db.session.commit()

        stripe = get_stripe()
        if not stripe:
            flash("Stripe isn't configured yet — add STRIPE_SECRET_KEY to enable checkout.", "error")
            return redirect(url_for("contacts.view_contact", contact_id=contact.id))

        session_kwargs = dict(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": item.name},
                    "unit_amount": item.price_cents,
                },
                "quantity": 1,
            }],
            success_url=(
                url_for("orders.order_success", order_id=order.id, _external=True)
                + "?session_id={CHECKOUT_SESSION_ID}"
            ),
            cancel_url=url_for("orders.order_cancelled", order_id=order.id, _external=True),
            metadata={"order_id": order.id},
        )

        if fulfillment_method == "shipping":
            session_kwargs["shipping_address_collection"] = {"allowed_countries": ["US"]}
            session_kwargs["shipping_options"] = [{
                "shipping_rate_data": {
                    "type": "fixed_amount",
                    "fixed_amount": {"amount": flat_rate, "currency": "usd"},
                    "display_name": "Standard shipping",
                }
            }]

        try:
            checkout_session = stripe.checkout.Session.create(**session_kwargs)
        except Exception as e:
            current_app.logger.error("Stripe checkout session creation failed: %s", e)
            flash("Couldn't start checkout — please try again.", "error")
            return redirect(url_for("contacts.view_contact", contact_id=contact.id))

        order.stripe_checkout_session_id = checkout_session.id
        db.session.commit()

        return redirect(checkout_session.url, code=303)

    return render_template(
        "orders/new.html",
        contact=contact,
        item=item,
        flat_rate_cents=flat_rate,
        can_dropoff=contact.org.can_offer_dropoff(),
    )


@contacts_bp.route("/<contact_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contact(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    custom_fields = _visible_custom_fields()
    badges = _visible_badges()

    if request.method == "GET":
        spouse = next((p for p in contact.people if p.household_role == "spouse"), None)
        custom_values = {v.field_definition_id: v.value for v in contact.custom_values}
        contact_badge_ids = {b.id for b in contact.badges}
        action_log_count = ActionLog.query.filter_by(contact_id=contact.id).count()
        org_members = (
            User.query.filter_by(org_id=current_user.org_id, status="active")
            .order_by(User.first_name, User.last_name)
            .all()
            if current_user.is_admin
            else []
        )
        return render_template(
            "contacts/edit.html",
            contact=contact,
            head=contact.primary_person(),
            spouse=spouse,
            custom_fields=custom_fields,
            custom_values=custom_values,
            badges=badges,
            contact_badge_ids=contact_badge_ids,
            action_log_count=action_log_count,
            org_members=org_members,
        )

    old_household_name = contact.household_name
    old_status = contact.status
    old_owner_id = contact.owner_user_id
    old_owner_name = contact.owner.full_name if contact.owner else "Shared"
    old_do_not_contact = contact.do_not_contact
    old_marketing_opt_out = contact.marketing_opt_out

    contact.household_name = request.form["household_name"]
    contact.status = request.form.get("status", contact.status)
    contact.notes = request.form.get("notes", "").strip() or None
    contact.marketing_opt_out = bool(request.form.get("marketing_opt_out"))
    contact.do_not_contact = bool(request.form.get("do_not_contact"))

    if request.form.get("remove_photo") == "1" and contact.photo_url:
        delete_contact_photo(contact.photo_url)
        contact.photo_url = None

    photo = request.files.get("photo")
    if photo and photo.filename:
        try:
            old_photo_url = contact.photo_url
            contact.photo_url = upload_contact_photo(photo, contact.id)
            if old_photo_url:
                delete_contact_photo(old_photo_url)
        except StorageError as exc:
            flash(str(exc), "error")
            return redirect(url_for("contacts.edit_contact", contact_id=contact.id))

    if current_user.is_admin:
        new_owner_id = request.form.get("owner_user_id", "").strip()
        if not new_owner_id:
            contact.owner_user_id = None
        else:
            new_owner = User.query.filter_by(id=new_owner_id, org_id=current_user.org_id).first()
            if new_owner:
                contact.owner_user_id = new_owner.id
    else:
        contact.owner_user_id = current_user.id if request.form.get("keep_private") else None

    head = contact.primary_person()
    head.first_name = request.form["head_first_name"]
    head.last_name = request.form["head_last_name"]
    _sync_contact_method(head.id, "email", "personal", request.form.get("head_email", "").strip())
    _sync_contact_method(head.id, "phone", "mobile", request.form.get("head_phone", "").strip())

    spouse = next((p for p in contact.people if p.household_role == "spouse"), None)
    spouse_first = request.form.get("spouse_first_name", "").strip()
    if spouse_first:
        if not spouse:
            spouse = ContactPerson(
                contact_id=contact.id, first_name=spouse_first,
                last_name=request.form.get("spouse_last_name") or head.last_name,
                household_role="spouse",
            )
            db.session.add(spouse)
            db.session.flush()
        else:
            spouse.first_name = spouse_first
            spouse.last_name = request.form.get("spouse_last_name") or head.last_name
        _sync_contact_method(spouse.id, "email", "personal", request.form.get("spouse_email", "").strip())
        _sync_contact_method(spouse.id, "phone", "mobile", request.form.get("spouse_phone", "").strip())
    elif spouse:
        db.session.delete(spouse)

    _save_custom_field_values(contact, request.form, custom_fields)
    _save_contact_badges(contact, request.form, badges)

    changes = []
    if old_household_name != contact.household_name:
        changes.append(f"Renamed from '{old_household_name}' to '{contact.household_name}'.")
    if old_status != contact.status:
        changes.append(f"Status changed from {old_status} to {contact.status}.")
    if old_owner_id != contact.owner_user_id:
        new_owner_obj = User.query.get(contact.owner_user_id) if contact.owner_user_id else None
        new_owner_name = new_owner_obj.full_name if new_owner_obj else "Shared"
        changes.append(f"Reassigned from {old_owner_name} to {new_owner_name}.")
    if old_marketing_opt_out != contact.marketing_opt_out:
        changes.append(
            "Opted out of marketing." if contact.marketing_opt_out else "Opted back into marketing."
        )
    if old_do_not_contact != contact.do_not_contact:
        if contact.do_not_contact:
            cancelled = (
                SuggestedAction.query
                .filter_by(contact_id=contact.id, status="pending")
                .all()
            )
            for suggestion in cancelled:
                suggestion.status = "skipped"
                suggestion.resolved_at = datetime.utcnow()
            changes.append(
                f"Marked Do Not Contact. Cancelled {len(cancelled)} pending suggestion{'s' if len(cancelled) != 1 else ''}."
                if cancelled else "Marked Do Not Contact."
            )
        else:
            changes.append("Removed Do Not Contact.")

    if changes:
        action = "reassigned" if old_owner_id != contact.owner_user_id and len(changes) == 1 else (
            "status_changed" if old_status != contact.status and len(changes) == 1 else "updated"
        )
        _log_contact_activity(contact, action, " ".join(changes))
    else:
        _log_contact_activity(contact, "updated", "Contact details updated.")

    db.session.commit()
    flash(f"Updated {contact.household_name}.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))


@contacts_bp.route("/<contact_id>/delete", methods=["POST"])
@login_required
def delete_contact(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()

    action_log_count = ActionLog.query.filter_by(contact_id=contact.id).count()
    if action_log_count and request.form.get("confirm_delete_history") != "1":
        flash(
            f"{contact.household_name} has {action_log_count} sent-gift record(s) on file "
            f"(used for spend/tax tracking). Check the confirmation box to delete them along "
            f"with this contact.",
            "error",
        )
        return redirect(url_for("contacts.edit_contact", contact_id=contact.id))

    # ActionLog.suggested_action_id is a FK to suggested_actions.id, so the
    # ActionLog rows (child) must be cleared before the SuggestedAction rows
    # (parent) they may reference, or the SuggestedAction delete fails with an
    # FK constraint error.
    if action_log_count:
        ActionLog.query.filter_by(contact_id=contact.id).delete()

    # ContactAuditLog.suggested_action_id is also a FK to suggested_actions.id
    # (added later, for the delete/undelete-suggestion Undo button, then
    # reused by qualified/expired/superseded audit entries) -- same problem
    # as ActionLog above, just missed when that FK was added. Detach it here
    # rather than deleting the audit rows: they're kept (see the contact_id
    # detachment below), just losing the direct link to a suggestion that's
    # about to stop existing.
    ContactAuditLog.query.filter_by(contact_id=contact.id).update({"suggested_action_id": None})
    SuggestedAction.query.filter_by(contact_id=contact.id).delete()

    name = contact.household_name
    photo_url_to_delete = contact.photo_url
    _log_contact_activity(contact, "deleted", f"Deleted by {current_user.full_name}.")
    db.session.flush()
    # Preserve the audit trail (via the denormalized name/actor snapshots) but
    # detach it from the contact_id FK so the delete below doesn't get blocked.
    ContactAuditLog.query.filter_by(contact_id=contact.id).update({"contact_id": None})

    db.session.delete(contact)
    db.session.commit()
    if photo_url_to_delete:
        delete_contact_photo(photo_url_to_delete)
    flash(f"{name} has been deleted.", "success")
    return redirect(url_for("contacts.list_contacts"))


@contacts_bp.route("/activity")
@admin_required
def activity_feed():
    entries = (
        ContactAuditLog.query.filter_by(org_id=current_user.org_id)
        .order_by(ContactAuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template("contacts/activity.html", entries=entries)


@contacts_bp.route("/fields")
@login_required
def manage_fields():
    org_fields = CustomFieldDefinition.query.filter_by(
        org_id=current_user.org_id, scope="org"
    ).order_by(CustomFieldDefinition.label).all()
    my_fields = CustomFieldDefinition.query.filter_by(
        org_id=current_user.org_id, scope="personal", owner_user_id=current_user.id
    ).order_by(CustomFieldDefinition.label).all()
    return render_template(
        "contacts/fields.html",
        org_fields=org_fields,
        my_fields=my_fields,
        field_types=CUSTOM_FIELD_TYPES,
    )


@contacts_bp.route("/fields/new", methods=["POST"])
@login_required
def new_field():
    scope = request.form.get("scope", "personal")
    if scope == "org" and not current_user.is_admin:
        flash("Only an admin can add an organization-wide field.", "error")
        return redirect(url_for("contacts.manage_fields"))

    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the field a name.", "error")
        return redirect(url_for("contacts.manage_fields"))

    field = CustomFieldDefinition(
        org_id=current_user.org_id,
        scope=scope,
        owner_user_id=None if scope == "org" else current_user.id,
        label=label,
        field_type=request.form.get("field_type", "text"),
        options=request.form.get("options", "").strip() or None,
    )
    db.session.add(field)
    db.session.commit()
    flash(f"Added the '{field.label}' field.", "success")
    return redirect(url_for("contacts.manage_fields"))


@contacts_bp.route("/fields/<field_id>/delete", methods=["POST"])
@login_required
def delete_field(field_id):
    field = CustomFieldDefinition.query.filter_by(
        id=field_id, org_id=current_user.org_id
    ).first_or_404()

    if field.scope == "org" and not current_user.is_admin:
        flash("Only an admin can remove an organization-wide field.", "error")
        return redirect(url_for("contacts.manage_fields"))
    if field.scope == "personal" and field.owner_user_id != current_user.id:
        flash("You can only remove your own personal fields.", "error")
        return redirect(url_for("contacts.manage_fields"))

    label = field.label
    db.session.delete(field)
    db.session.commit()
    flash(f"Removed the '{label}' field, along with its saved values on every contact.", "success")
    return redirect(url_for("contacts.manage_fields"))


@contacts_bp.route("/badges")
@login_required
def manage_badges():
    global_badges = Badge.query.filter_by(scope="global").order_by(Badge.label).all()
    my_badges = Badge.query.filter_by(
        scope="personal", owner_user_id=current_user.id
    ).order_by(Badge.label).all()
    return render_template(
        "contacts/badges.html",
        global_badges=global_badges,
        my_badges=my_badges,
    )


@contacts_bp.route("/badges/new", methods=["POST"])
@login_required
def new_badge():
    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the badge a name.", "error")
        return redirect(url_for("contacts.manage_badges"))

    badge = Badge(
        scope="personal",
        owner_user_id=current_user.id,
        org_id=current_user.org_id,
        label=label,
        color=request.form.get("color", "").strip() or None,
    )
    db.session.add(badge)
    db.session.commit()
    flash(f"Added the '{badge.label}' badge.", "success")
    return redirect(url_for("contacts.manage_badges"))


@contacts_bp.route("/badges/<badge_id>/edit", methods=["POST"])
@login_required
def edit_badge(badge_id):
    badge = Badge.query.filter_by(id=badge_id, scope="personal", owner_user_id=current_user.id).first_or_404()

    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the badge a name.", "error")
        return redirect(url_for("contacts.manage_badges"))

    badge.label = label
    badge.color = request.form.get("color", "").strip() or None
    db.session.commit()
    flash(f"Updated the '{badge.label}' badge. Every contact that already had it keeps it.", "success")
    return redirect(url_for("contacts.manage_badges"))


@contacts_bp.route("/badges/<badge_id>/delete", methods=["POST"])
@login_required
def delete_badge(badge_id):
    badge = Badge.query.filter_by(id=badge_id, scope="personal", owner_user_id=current_user.id).first_or_404()
    label = badge.label
    db.session.delete(badge)
    db.session.commit()
    flash(f"Removed the '{label}' badge from your contacts.", "success")
    return redirect(url_for("contacts.manage_badges"))


@contacts_bp.route("/event-types")
@login_required
def manage_event_types():
    org_types = CustomEventType.query.filter_by(
        org_id=current_user.org_id, scope="org"
    ).order_by(CustomEventType.label).all()
    my_types = CustomEventType.query.filter_by(
        org_id=current_user.org_id, scope="personal", owner_user_id=current_user.id
    ).order_by(CustomEventType.label).all()

    # Every milestone type this agent can currently use, for the
    # drag-and-drop priority list -- built-ins, org-wide, and their own
    # personal ones. Doesn't include other agents' personal milestones,
    # since this agent's contacts can never actually surface those.
    all_types = {t: t.replace("_", " ").title() for t in STANDARD_EVENT_TYPES if t != "custom"}
    for t in org_types:
        all_types[t.key] = t.label
    for t in my_types:
        all_types[t.key] = t.label

    ranked = (
        MilestonePriority.query.filter_by(user_id=current_user.id)
        .order_by(MilestonePriority.priority.desc())
        .all()
    )
    ranked_keys = [r.event_type for r in ranked]
    # Anything the agent hasn't ranked yet (never customized, or a
    # milestone added since they last saved) is appended after their
    # ranked ones, in a stable default order -- so the list always shows
    # every usable type, even before the agent has touched this page.
    unranked_keys = [k for k in all_types if k not in ranked_keys]
    priority_order = [
        {"key": k, "label": all_types[k]}
        for k in ranked_keys + unranked_keys
        if k in all_types  # drop stale ranked rows for a since-removed milestone
    ]

    return render_template(
        "contacts/event_types.html",
        org_types=org_types,
        my_types=my_types,
        standard_event_types=[t for t in STANDARD_EVENT_TYPES if t != "custom"],
        priority_order=priority_order,
        has_custom_priority=bool(ranked_keys),
    )


@contacts_bp.route("/event-types/priority", methods=["POST"])
@login_required
def save_milestone_priority():
    """Persists the agent's full drag-and-drop order in one shot -- top
    of the list gets the highest priority number, counting down. Always
    replaces this agent's entire MilestonePriority set rather than
    patching individual rows, since a full reorder is what the UI
    actually produces and it keeps the "gaps are meaningless, only
    relative order matters" invariant simple."""
    ordered_keys = request.form.getlist("order")
    if not ordered_keys:
        flash("Nothing to save.", "error")
        return redirect(url_for("contacts.manage_event_types"))

    MilestonePriority.query.filter_by(user_id=current_user.id).delete()
    total = len(ordered_keys)
    for i, key in enumerate(ordered_keys):
        db.session.add(MilestonePriority(
            user_id=current_user.id,
            event_type=key,
            priority=total - i,
        ))
    db.session.commit()
    flash("Milestone priority order saved.", "success")
    return redirect(url_for("contacts.manage_event_types"))


@contacts_bp.route("/event-types/new", methods=["POST"])
@login_required
def new_event_type():
    scope = request.form.get("scope", "personal")
    if scope == "org" and not current_user.is_admin:
        flash("Only an admin can add an organization-wide milestone.", "error")
        return redirect(url_for("contacts.manage_event_types"))

    label = request.form.get("label", "").strip()
    if not label:
        flash("Give the milestone a name.", "error")
        return redirect(url_for("contacts.manage_event_types"))

    key = slugify_event_key(label)
    if key in STANDARD_EVENT_TYPES or CustomEventType.query.filter_by(
        org_id=current_user.org_id, key=key
    ).first():
        flash(f"A milestone already exists with a name too similar to '{label}'. Try something more distinct.", "error")
        return redirect(url_for("contacts.manage_event_types"))

    event_type = CustomEventType(
        org_id=current_user.org_id,
        scope=scope,
        owner_user_id=None if scope == "org" else current_user.id,
        key=key,
        label=label,
    )
    db.session.add(event_type)
    db.session.commit()
    flash(f"Added the '{event_type.label}' milestone.", "success")
    return redirect(url_for("contacts.manage_event_types"))


@contacts_bp.route("/event-types/<event_type_id>/delete", methods=["POST"])
@login_required
def delete_event_type(event_type_id):
    event_type = CustomEventType.query.filter_by(
        id=event_type_id, org_id=current_user.org_id
    ).first_or_404()

    if event_type.scope == "org" and not current_user.is_admin:
        flash("Only an admin can remove an organization-wide milestone.", "error")
        return redirect(url_for("contacts.manage_event_types"))
    if event_type.scope == "personal" and event_type.owner_user_id != current_user.id:
        flash("You can only remove your own personal milestones.", "error")
        return redirect(url_for("contacts.manage_event_types"))

    label = event_type.label
    db.session.delete(event_type)
    db.session.commit()
    flash(
        f"Removed the '{label}' milestone. Timeline events and flows already using it are unaffected "
        "-- it just won't be offered as an option going forward.",
        "success",
    )
    return redirect(url_for("contacts.manage_event_types"))


@contacts_bp.route("/<contact_id>/timeline/new", methods=["POST"])
@login_required
def add_timeline_event(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()

    event = TimelineEvent(
        contact_id=contact.id,
        event_type=request.form["event_type"],
        label=request.form.get("label") or None,
        event_date=datetime.strptime(request.form["event_date"], "%Y-%m-%d").date(),
        notes=request.form.get("notes"),
        is_recurring=bool(request.form.get("is_recurring")),
        recurrence_rule="annual" if request.form.get("is_recurring") else "none",
    )
    db.session.add(event)
    _log_contact_activity(
        contact, "timeline_added",
        f"Added timeline event: {event.display_label()} on {event.event_date.isoformat()}.",
    )
    db.session.commit()
    flash("Timeline event added.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))


@contacts_bp.route("/<contact_id>/timeline/<event_id>/edit", methods=["POST"])
@login_required
def edit_timeline_event(contact_id, event_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    event = TimelineEvent.query.filter_by(id=event_id, contact_id=contact.id).first_or_404()

    event.event_type = request.form["event_type"]
    event.label = request.form.get("label") or None
    event.event_date = datetime.strptime(request.form["event_date"], "%Y-%m-%d").date()
    event.notes = request.form.get("notes")
    event.is_recurring = bool(request.form.get("is_recurring"))
    event.recurrence_rule = "annual" if event.is_recurring else "none"

    _log_contact_activity(
        contact, "timeline_updated",
        f"Updated timeline event: {event.display_label()} on {event.event_date.isoformat()}.",
    )
    db.session.commit()
    flash("Timeline event updated.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))


@contacts_bp.route("/<contact_id>/timeline/<event_id>/delete", methods=["POST"])
@login_required
def delete_timeline_event(contact_id, event_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    event = TimelineEvent.query.filter_by(id=event_id, contact_id=contact.id).first_or_404()

    label = event.display_label()
    event_date = event.event_date.isoformat()

    # Any SuggestedAction (pending or historical) that this event triggered
    # points back at it via triggering_event_id. Detach rather than block or
    # cascade: the suggestion/gift/audit history stays intact, it just loses
    # the "why" link back to an event that no longer exists.
    linked_actions = SuggestedAction.query.filter_by(triggering_event_id=event.id).all()
    for action in linked_actions:
        action.triggering_event_id = None

    db.session.delete(event)
    _log_contact_activity(
        contact, "timeline_deleted",
        f"Deleted timeline event: {label} on {event_date}."
        + (f" ({len(linked_actions)} linked suggestion(s) kept, no longer linked to this event.)"
           if linked_actions else ""),
    )
    db.session.commit()
    flash("Timeline event deleted.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))
