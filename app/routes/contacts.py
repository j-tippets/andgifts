from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import (
    Contact, ContactPerson, ContactMethod,
    TimelineEvent, STANDARD_EVENT_TYPES,
    CustomFieldDefinition, CustomFieldValue, CUSTOM_FIELD_TYPES,
    SuggestedAction, ActionLog, User, ContactAuditLog,
)
from app.decorators import admin_required

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


@contacts_bp.route("/<contact_id>")
@login_required
def view_contact(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    custom_values = {v.field_definition_id: v.value for v in contact.custom_values}
    recent_activity = (
        ContactAuditLog.query.filter_by(contact_id=contact.id)
        .order_by(ContactAuditLog.created_at.desc())
        .limit(15)
        .all()
    )
    return render_template(
        "contacts/view.html",
        contact=contact,
        event_types=STANDARD_EVENT_TYPES,
        custom_fields=_visible_custom_fields(),
        custom_values=custom_values,
        recent_activity=recent_activity,
    )


@contacts_bp.route("/<contact_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contact(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    custom_fields = _visible_custom_fields()

    if request.method == "GET":
        spouse = next((p for p in contact.people if p.household_role == "spouse"), None)
        custom_values = {v.field_definition_id: v.value for v in contact.custom_values}
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
            action_log_count=action_log_count,
            org_members=org_members,
        )

    old_household_name = contact.household_name
    old_status = contact.status
    old_owner_id = contact.owner_user_id
    old_owner_name = contact.owner.full_name if contact.owner else "Shared"

    contact.household_name = request.form["household_name"]
    contact.status = request.form.get("status", contact.status)
    contact.notes = request.form.get("notes", "").strip() or None
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

    changes = []
    if old_household_name != contact.household_name:
        changes.append(f"Renamed from '{old_household_name}' to '{contact.household_name}'.")
    if old_status != contact.status:
        changes.append(f"Status changed from {old_status} to {contact.status}.")
    if old_owner_id != contact.owner_user_id:
        new_owner_obj = User.query.get(contact.owner_user_id) if contact.owner_user_id else None
        new_owner_name = new_owner_obj.full_name if new_owner_obj else "Shared"
        changes.append(f"Reassigned from {old_owner_name} to {new_owner_name}.")

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

    SuggestedAction.query.filter_by(contact_id=contact.id).delete()
    if action_log_count:
        ActionLog.query.filter_by(contact_id=contact.id).delete()

    name = contact.household_name
    _log_contact_activity(contact, "deleted", f"Deleted by {current_user.full_name}.")
    db.session.flush()
    # Preserve the audit trail (via the denormalized name/actor snapshots) but
    # detach it from the contact_id FK so the delete below doesn't get blocked.
    ContactAuditLog.query.filter_by(contact_id=contact.id).update({"contact_id": None})

    db.session.delete(contact)
    db.session.commit()
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
