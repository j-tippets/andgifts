from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import (
    Contact, ContactPerson, ContactMethod, Interest,
    TimelineEvent, STANDARD_EVENT_TYPES,
)

contacts_bp = Blueprint("contacts", __name__, url_prefix="/contacts")


@contacts_bp.route("/")
@login_required
def list_contacts():
    status_filter = request.args.get("status")
    query = Contact.query.filter_by(org_id=current_user.org_id)
    query = Contact.visible_to(query, current_user)
    if status_filter in ("new", "active", "past"):
        query = query.filter_by(status=status_filter)
    contacts = query.order_by(Contact.household_name).all()
    return render_template("contacts/list.html", contacts=contacts, status_filter=status_filter)


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
        all_interests = Interest.query.order_by(Interest.name).all()
        return render_template("contacts/new.html", interests=all_interests)

    if not org.can_add_contact():
        flash("Contact limit reached for your plan.", "error")
        return redirect(url_for("contacts.list_contacts"))

    contact = Contact(
        org_id=org.id,
        household_name=request.form["household_name"],
        status=request.form.get("status", "new"),
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

    # Interests (checkboxes, list of interest ids)
    interest_ids = request.form.getlist("interests")
    if interest_ids:
        contact.interests = Interest.query.filter(Interest.id.in_(interest_ids)).all()

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


def _add_contact_methods(person_id, form, prefix):
    email = form.get(f"{prefix}_email")
    phone = form.get(f"{prefix}_phone")
    if email:
        db.session.add(ContactMethod(person_id=person_id, method_type="email", subtype="personal", value=email, is_primary=True))
    if phone:
        db.session.add(ContactMethod(person_id=person_id, method_type="phone", subtype="mobile", value=phone, is_primary=True))


@contacts_bp.route("/<contact_id>")
@login_required
def view_contact(contact_id):
    query = Contact.query.filter_by(id=contact_id, org_id=current_user.org_id)
    contact = Contact.visible_to(query, current_user).first_or_404()
    return render_template("contacts/view.html", contact=contact, event_types=STANDARD_EVENT_TYPES)


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
    db.session.commit()
    flash("Timeline event added.", "success")
    return redirect(url_for("contacts.view_contact", contact_id=contact.id))
