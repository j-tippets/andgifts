"""Importing contacts from a CSV export.

An agent arriving from Follow Up Boss, kvCORE, Sierra, or a spreadsheet
has hundreds of contacts and will not retype them. Nothing in the app
reads a CSV today, which makes "try &Gifts" mean "spend an evening on
data entry first" -- a conversion blocker rather than a bug.

Design notes worth knowing before changing this:

**UI-agnostic on purpose.** Contact creation currently lives inline in
routes/contacts.new_contact, welded to request.form and current_user.
Writing a second copy here would guarantee the two drift -- the same
mistake that produced the deletion bugs, where two routes each did
their own partial cleanup and each missed different tables. Everything
here takes plain dicts and an explicit acting user, so the CLI, a
future web upload page, and tests all drive one implementation.

**Preview is the same code path as commit.** `dry_run=True` runs every
parse, validation and duplicate check and then rolls back. A preview
that runs different logic than the real thing is worse than no preview,
because it builds confidence in an answer that was never tested.

**Nothing is guessed silently.** A row that can't be understood is
reported with its line number and left out, rather than imported as
"Unknown Unknown". An agent can fix a spreadsheet; they cannot fix 400
half-imported records they don't know about.

**Deduplication is by email first, name second.** Email is the only
field in a typical CRM export that is both reliably present and
actually unique. Household name collides constantly ("The Smiths"), so
it is only used when no email is available at all, and even then only
within the importing org.
"""
import csv
import io
import re
from datetime import date, datetime

from app.extensions import db
from app.models import (
    Contact,
    ContactMethod,
    ContactPerson,
    CustomEventType,
    CustomFieldDefinition,
    CustomFieldValue,
    TimelineEvent,
)

# Header aliases, lowercased and stripped of non-alphanumerics before
# matching, so "First Name", "first_name" and "FIRSTNAME" all collapse
# to the same key. Drawn from actual export headers of the CRMs agents
# are most likely to be leaving.
COLUMN_ALIASES = {
    "household_name": [
        "householdname", "household", "clientname", "displayname", "name",
        "fullname", "contactname", "lastname",
    ],
    "head_first_name": ["firstname", "first", "givenname", "headfirstname", "primaryfirstname"],
    "head_last_name": ["lastname", "last", "surname", "familyname", "headlastname", "primarylastname"],
    "head_email": ["email", "emailaddress", "primaryemail", "email1", "homeemail", "workemail"],
    "head_phone": ["phone", "phonenumber", "mobile", "cellphone", "cell", "primaryphone", "phone1"],
    "spouse_first_name": ["spousefirstname", "partnerfirstname", "secondaryfirstname", "spousefirst"],
    "spouse_last_name": ["spouselastname", "partnerlastname", "secondarylastname", "spouselast"],
    "spouse_email": ["spouseemail", "partneremail", "secondaryemail", "email2"],
    "spouse_phone": ["spousephone", "partnerphone", "secondaryphone", "phone2"],
    "shipping_address_line1": ["address", "addressline1", "address1", "street", "streetaddress", "mailingaddress"],
    "shipping_address_line2": ["addressline2", "address2", "unit", "apt", "suite"],
    "shipping_city": ["city", "town", "mailingcity"],
    "shipping_state": ["state", "province", "region", "mailingstate"],
    "shipping_zip": ["zip", "zipcode", "postalcode", "postcode", "mailingzip"],
    "notes": ["notes", "note", "comments", "description", "background"],
}

# Date columns become TimelineEvents rather than plain fields, because
# that is what the suggestion engine reads. The value is a candidate
# CustomEventType key; it is only used if the importing org actually has
# that event type (see _resolve_event_types) -- inventing event types
# during an import would silently change the org's configuration.
DATE_COLUMN_ALIASES = {
    "birthday": ["birthday", "birthdate", "dateofbirth", "dob", "headbirthday"],
    "closing": ["closingdate", "closedate", "settlementdate", "transactiondate", "closing"],
    "home_anniversary": ["homeanniversary", "houseanniversary", "purchaseanniversary", "homeaversary"],
    "wedding_anniversary": ["anniversary", "weddinganniversary", "marriagedate"],
}

MAX_ROWS = 5000

# Any leap year works; it only has to be one, so Feb 29 round-trips.
LEAP_PLACEHOLDER_YEAR = 2000


def _normalize_header(header):
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def detect_columns(headers):
    """Maps CSV headers onto known fields.

    Returns (mapping, date_mapping, unmapped) where mapping is
    {field: header}. First match wins, and a header already claimed by
    an earlier field is not reused -- "lastname" appears under both
    household_name and head_last_name because some exports use it as
    the household label and others as a person's surname, and claiming
    it once avoids one column silently populating two fields.
    """
    normalized = {_normalize_header(h): h for h in headers if h}
    claimed = set()
    mapping = {}
    date_mapping = {}

    # head_last_name before household_name: when a file has a single
    # "Last Name" column it is far more often a person's surname, and
    # household_name can be derived from it (see _household_name_for).
    field_order = [
        "head_first_name", "head_last_name", "head_email", "head_phone",
        "spouse_first_name", "spouse_last_name", "spouse_email", "spouse_phone",
        "household_name",
        "shipping_address_line1", "shipping_address_line2",
        "shipping_city", "shipping_state", "shipping_zip", "notes",
    ]
    for field in field_order:
        for alias in COLUMN_ALIASES[field]:
            if alias in normalized and normalized[alias] not in claimed:
                mapping[field] = normalized[alias]
                claimed.add(normalized[alias])
                break

    for event_key, aliases in DATE_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized and normalized[alias] not in claimed:
                date_mapping[event_key] = normalized[alias]
                claimed.add(normalized[alias])
                break

    unmapped = [h for h in headers if h and h not in claimed]
    return mapping, date_mapping, unmapped


# (format, separator) -- the separator is what joins the year-less value
# to LEAP_PLACEHOLDER_YEAR before parsing.
YEARLESS_FORMATS = (
    ("%m/%d/%Y", "/"),
    ("%m-%d-%Y", "-"),
    ("%B %d %Y", " "),
    ("%b %d %Y", " "),
)

DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y/%m/%d",
)


def parse_date(raw):
    """Returns (date, year_known) or (None, None).

    year_known matters: TimelineEvent models a birthday whose year
    nobody recorded, which is extremely common in CRM exports. A
    month/day-only value is stored against a placeholder year with
    year_known=False rather than being dropped, so the suggestion engine
    can still fire on it.
    """
    value = (raw or "").strip()
    if not value:
        return None, None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date(), True
        except ValueError:
            continue

    # Year-less values get a leap year appended before parsing rather
    # than being parsed bare. strptime defaults a missing year to 1900,
    # which is NOT a leap year, so "02/29" raises ValueError and a
    # Feb-29 birthday would be silently dropped -- the one date most
    # likely to be entered deliberately.
    for fmt, separator in YEARLESS_FORMATS:
        try:
            with_year = f"{value}{separator}{LEAP_PLACEHOLDER_YEAR}"
            return datetime.strptime(with_year, fmt).date(), False
        except ValueError:
            continue

    return None, None


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean(value):
    return (value or "").strip() or None


def _clean_email(value):
    email = (value or "").strip().lower()
    return email if email and EMAIL_RE.match(email) else None


def _household_name_for(row):
    """What the agent will see in their contact list.

    Prefers an explicit household column, then "The Smiths" built from a
    surname, then the person's own name. Never returns a placeholder --
    a row with nothing usable is an error, not a contact called
    "Unknown"."""
    explicit = _clean(row.get("household_name"))
    if explicit:
        return explicit

    last = _clean(row.get("head_last_name"))
    if last:
        return f"The {last}s" if not last.lower().endswith("s") else f"The {last}"

    first = _clean(row.get("head_first_name"))
    return first or None


class RowResult:
    """One row's outcome, for the caller's report."""

    def __init__(self, line_number, status, household_name=None, reason=None):
        self.line_number = line_number
        # "created" | "updated" | "duplicate" | "error" | "over_limit"
        self.status = status
        self.household_name = household_name
        self.reason = reason

    def __repr__(self):
        return f"<RowResult line={self.line_number} {self.status} {self.reason or ''}>"


class ImportReport:
    def __init__(self):
        self.rows = []
        self.mapping = {}
        self.date_mapping = {}
        self.custom_field_mapping = {}
        self.unmapped_headers = []
        self.dry_run = True

    def add(self, result):
        self.rows.append(result)

    def _count(self, status):
        return sum(1 for r in self.rows if r.status == status)

    @property
    def created(self):
        return self._count("created")

    @property
    def duplicates(self):
        return self._count("duplicate")

    @property
    def updated(self):
        return self._count("updated")

    @property
    def errors(self):
        return [r for r in self.rows if r.status == "error"]

    @property
    def over_limit(self):
        return self._count("over_limit")

    def summary(self):
        return {
            "created": self.created,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "errors": len(self.errors),
            "over_limit": self.over_limit,
            "dry_run": self.dry_run,
        }


def _existing_emails_for_org(org_id):
    """{lowercased email: contact_id} for everything this org already has.

    A map rather than a set because a matched row is no longer simply
    skipped -- it can fill blanks on the contact it matched, so the
    match has to identify *which* contact.

    One query rather than a lookup per row: a 400-row import would
    otherwise issue 400 queries, and this runs inside a request in the
    web version.
    """
    rows = (
        db.session.query(ContactMethod.value, Contact.id)
        .join(ContactPerson, ContactMethod.person_id == ContactPerson.id)
        .join(Contact, ContactPerson.contact_id == Contact.id)
        .filter(Contact.org_id == org_id, ContactMethod.method_type == "email")
        .all()
    )
    return {(value or "").strip().lower(): contact_id for value, contact_id in rows if value}


def _existing_household_names_for_org(org_id):
    rows = db.session.query(Contact.household_name, Contact.id).filter(
        Contact.org_id == org_id
    ).all()
    return {(name or "").strip().lower(): contact_id for name, contact_id in rows if name}


def _resolve_event_types(org_id, date_mapping):
    """Which of the detected date columns this org can actually store.

    An import must not create CustomEventTypes -- that would silently
    reconfigure the org from the contents of a spreadsheet. Columns
    whose event type doesn't exist are reported as unmapped instead.
    """
    if not date_mapping:
        return {}
    wanted = set(date_mapping)
    existing = {
        key for (key,) in db.session.query(CustomEventType.key)
        .filter(CustomEventType.org_id == org_id, CustomEventType.key.in_(wanted))
        .all()
    }
    return {k: v for k, v in date_mapping.items() if k in existing}


def _resolve_custom_fields(org, acting_user, headers, claimed):
    """Matches leftover CSV headers to this org's custom fields by label.

    Deliberately allowed to overlap with the milestone columns. A
    "Birthday" header can legitimately be both: a Milestone, which is
    what flows and gift suggestions trigger on, and a Custom Field,
    which is what shows on the contact record as plain data. An agency
    may have set up either, both, or neither, and a column that matches
    both should populate both rather than the import silently picking
    one. `claimed` therefore only excludes headers already taken by core
    fields (name, email, address), never by date_mapping.

    Scoped with CustomFieldDefinition.visible_to, so an agent's personal
    fields work for their own import without becoming visible to the
    rest of the agency.
    """
    query = CustomFieldDefinition.query.filter_by(org_id=org.id)
    fields = CustomFieldDefinition.visible_to(query, acting_user).all()
    by_label = {_normalize_header(f.label): f for f in fields}

    matched = {}
    for header in headers:
        if not header or header in claimed:
            continue
        field = by_label.get(_normalize_header(header))
        if field:
            matched[header] = field
    return matched


def _coerce_custom_value(field, raw):
    """Turns a CSV cell into the string the rest of the app expects.

    Stored formats have to match what routes/contacts._save_custom_field_values
    writes, or an imported value would display or edit differently from
    a hand-entered one -- checkbox as "1"/"0", date as ISO, select as
    one of the defined options.

    Returns None when there is nothing usable, which means "don't write
    a value" rather than "write an empty one".
    """
    value = (raw or "").strip()
    if not value:
        return None

    if field.field_type == "checkbox":
        return "1" if value.lower() in ("1", "true", "yes", "y", "x", "t") else "0"

    if field.field_type == "date":
        parsed, _year_known = parse_date(value)
        return parsed.isoformat() if parsed else None

    if field.field_type in ("number", "currency"):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        return cleaned or None

    if field.field_type == "select":
        # Only a defined option, matched case-insensitively. Storing
        # anything else would produce a value the edit dropdown can't
        # represent, so the agent couldn't see or fix it.
        for option in field.option_list():
            if option.lower() == value.lower():
                return option
        return None

    return value[:5000]


def import_contacts(csv_text, org, acting_user, owner_user=None, dry_run=True):
    """Imports contacts from CSV text into `org`.

    owner_user, when given, makes every imported contact private to that
    agent (Contact.owner_user_id). Left None, contacts are shared
    org-wide -- the same meaning that field has everywhere else.

    dry_run runs the entire import and rolls back, so a preview and a
    real run exercise identical code.

    Returns an ImportReport. Raises ValueError only for a file that
    can't be read as CSV at all.
    """
    report = ImportReport()
    report.dry_run = dry_run

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = reader.fieldnames or []
    except csv.Error as exc:
        raise ValueError(f"Couldn't read that file as CSV: {exc}") from exc

    if not headers:
        raise ValueError("That file has no header row, so there's nothing to map columns from.")

    mapping, date_mapping, unmapped = detect_columns(headers)
    usable_dates = _resolve_event_types(org.id, date_mapping)
    # Date columns the org has no event type for are unmapped in
    # practice, and the agent should be told rather than left assuming
    # their closing dates came across.
    for key, header in date_mapping.items():
        if key not in usable_dates:
            unmapped.append(header)

    # Custom fields are matched against everything the core mapping
    # didn't take -- including headers already used as milestones, so a
    # "Birthday" column can feed both.
    custom_fields = _resolve_custom_fields(org, acting_user, headers, set(mapping.values()))
    unmapped = [h for h in unmapped if h not in custom_fields]

    report.mapping = mapping
    report.date_mapping = usable_dates
    report.custom_field_mapping = {h: f.label for h, f in custom_fields.items()}
    report.unmapped_headers = unmapped

    if "head_first_name" not in mapping and "household_name" not in mapping:
        raise ValueError(
            "Couldn't find a name column. The file needs at least a first name "
            "or a household/client name column."
        )

    known_emails = _existing_emails_for_org(org.id)
    known_names = _existing_household_names_for_org(org.id)
    seen_in_file = set()

    limit = org.limit_for("contacts")
    current_count = org.contact_count()

    savepoint = db.session.begin_nested()
    try:
        for index, raw_row in enumerate(reader, start=2):  # line 1 is the header
            if len(report.rows) >= MAX_ROWS:
                report.add(RowResult(index, "error", reason=f"File exceeds the {MAX_ROWS}-row limit."))
                break

            row = {field: raw_row.get(header) for field, header in mapping.items()}
            household_name = _household_name_for(row)
            if not household_name:
                report.add(RowResult(index, "error", reason="No name in this row."))
                continue

            head_email = _clean_email(row.get("head_email"))
            raw_email = _clean(row.get("head_email"))
            if raw_email and not head_email:
                report.add(RowResult(
                    index, "error", household_name,
                    reason=f"'{raw_email}' doesn't look like an email address.",
                ))
                continue

            # Duplicate detection. Email is authoritative; household name
            # is a fallback only when there's no email to go on, since
            # "The Smiths" collides constantly.
            duplicate_key = head_email or household_name.lower()

            if duplicate_key in seen_in_file:
                report.add(RowResult(index, "duplicate", household_name,
                                     reason="Appears more than once in this file."))
                continue

            # A match is no longer simply skipped. The CSV may carry
            # something this contact doesn't have yet -- most obviously
            # a column that couldn't be mapped on an earlier import
            # because the milestone or custom field didn't exist then.
            # Blanks are filled; existing values are never touched.
            existing_id = known_emails.get(head_email) if head_email else None
            if existing_id is None and not head_email:
                existing_id = known_names.get(household_name.lower())

            if existing_id is not None:
                existing = db.session.get(Contact, existing_id)
                added = _fill_blanks(existing, row, raw_row, usable_dates, custom_fields) if existing else []
                seen_in_file.add(duplicate_key)
                if added:
                    report.add(RowResult(index, "updated", household_name,
                                         reason="Adding " + ", ".join(added) + "."))
                else:
                    report.add(RowResult(index, "duplicate", household_name,
                                         reason="Already up to date."))
                continue

            if limit is not None and current_count >= limit:
                report.add(RowResult(index, "over_limit", household_name,
                                     reason=f"Would exceed the plan's {limit}-contact limit."))
                continue

            contact = _create_contact_from_row(
                row, raw_row, usable_dates, custom_fields, org, acting_user, owner_user,
            )
            db.session.flush()

            seen_in_file.add(duplicate_key)
            if head_email:
                known_emails[head_email] = contact.id
            known_names[household_name.lower()] = contact.id
            current_count += 1
            report.add(RowResult(index, "created", household_name))

        if dry_run:
            savepoint.rollback()
        else:
            savepoint.commit()
            db.session.commit()
    except Exception:
        savepoint.rollback()
        raise

    return report


CONTACT_TEXT_FIELDS = (
    ("shipping_address_line1", "address"),
    ("shipping_address_line2", "address line 2"),
    ("shipping_city", "city"),
    ("shipping_state", "state"),
    ("shipping_zip", "ZIP"),
    ("notes", "notes"),
)


def _fill_blanks(contact, row, raw_row, usable_dates, custom_fields):
    """Adds anything the CSV has that this contact is missing. Never
    overwrites a value that is already there.

    Fill-blanks rather than overwrite, deliberately. A CRM export is
    usually a snapshot of the day it was downloaded. If re-importing
    overwrote, an agent who corrected a client's address in &Gifts and
    then re-uploaded last month's Follow Up Boss export would silently
    lose the correction -- with no undo, no bulk revert, and nothing to
    tell them it happened. The failure would surface months later as a
    gift shipped to an old address.

    So re-uploading the same file is always safe: it can only ever add.
    A true "my CRM is the source of truth" resync is a different
    feature and needs its own explicit opt-in, not this default.

    Returns a list of short human labels for what was added, which is
    what the preview shows per contact -- an agent approving an update
    should see exactly what it will change.
    """
    added = []

    for attribute, label in CONTACT_TEXT_FIELDS:
        if getattr(contact, attribute, None):
            continue
        value = _clean(row.get(attribute))
        if not value:
            continue
        if attribute == "shipping_state":
            value = value.upper()[:50]
        setattr(contact, attribute, value)
        added.append(label)

    head = (
        ContactPerson.query
        .filter_by(contact_id=contact.id, household_role="head")
        .first()
    )
    if head:
        # Only fills a genuinely blank name. The import writes "" for a
        # missing first or last name (both columns are NOT NULL), so
        # these are the rows a later, more complete export can repair.
        if not (head.first_name or "").strip() and _clean(row.get("head_first_name")):
            head.first_name = _clean(row.get("head_first_name"))
            added.append("first name")
        if not (head.last_name or "").strip() and _clean(row.get("head_last_name")):
            head.last_name = _clean(row.get("head_last_name"))
            added.append("last name")

        for method_type, subtype, value in (
            ("email", "personal", _clean_email(row.get("head_email"))),
            ("phone", "mobile", _clean(row.get("head_phone"))),
        ):
            if not value:
                continue
            exists = ContactMethod.query.filter_by(
                person_id=head.id, method_type=method_type
            ).first()
            if exists:
                continue
            db.session.add(ContactMethod(
                person_id=head.id, method_type=method_type, subtype=subtype,
                value=value, is_primary=True,
            ))
            added.append(method_type)

    for event_key, header in usable_dates.items():
        event_date, year_known = parse_date(raw_row.get(header))
        if not event_date:
            continue
        # Keyed on event_type, not on the date: a contact who already
        # has a birthday recorded keeps the one the agent has, even if
        # the spreadsheet disagrees.
        exists = TimelineEvent.query.filter_by(
            contact_id=contact.id, event_type=event_key
        ).first()
        if exists:
            continue
        db.session.add(TimelineEvent(
            contact_id=contact.id,
            event_type=event_key,
            event_date=event_date,
            year_known=year_known,
            is_recurring=True,
            recurrence_rule="annual",
            is_important_date=True,
        ))
        added.append(event_key.replace("_", " "))

    for header, field in custom_fields.items():
        value = _coerce_custom_value(field, raw_row.get(header))
        if value is None:
            continue
        exists = CustomFieldValue.query.filter_by(
            contact_id=contact.id, field_definition_id=field.id
        ).first()
        if exists and (exists.value or "").strip():
            continue
        if exists:
            exists.value = value
        else:
            db.session.add(CustomFieldValue(
                contact_id=contact.id, field_definition_id=field.id, value=value,
            ))
        added.append(field.label)

    return added


def _create_contact_from_row(row, raw_row, usable_dates, custom_fields, org, acting_user, owner_user):
    """Builds one Contact and its people/methods/events.

    Mirrors routes/contacts.new_contact deliberately, including the
    first_contact TimelineEvent it seeds, so an imported contact behaves
    identically to a hand-entered one everywhere downstream -- the
    suggestion engine, reports, and the contact page all assume that
    event exists.
    """
    contact = Contact(
        org_id=org.id,
        owner_user_id=owner_user.id if owner_user else None,
        household_name=_household_name_for(row),
        status="new",
        notes=_clean(row.get("notes")),
        shipping_address_line1=_clean(row.get("shipping_address_line1")),
        shipping_address_line2=_clean(row.get("shipping_address_line2")),
        shipping_city=_clean(row.get("shipping_city")),
        shipping_state=(_clean(row.get("shipping_state")) or "").upper()[:50] or None,
        shipping_zip=_clean(row.get("shipping_zip")),
    )
    db.session.add(contact)
    db.session.flush()

    head_first = _clean(row.get("head_first_name"))
    head_last = _clean(row.get("head_last_name"))
    # Both are NOT NULL on ContactPerson, and a CRM export frequently
    # has only one of them. An empty string keeps the row importable
    # without inventing a name the agent would have to hunt down later.
    head = ContactPerson(
        contact_id=contact.id,
        first_name=head_first or "",
        last_name=head_last or "",
        household_role="head",
    )
    db.session.add(head)
    db.session.flush()
    _add_methods(head.id, _clean_email(row.get("head_email")), _clean(row.get("head_phone")))

    spouse_first = _clean(row.get("spouse_first_name"))
    if spouse_first:
        spouse = ContactPerson(
            contact_id=contact.id,
            first_name=spouse_first,
            last_name=_clean(row.get("spouse_last_name")) or head_last or "",
            household_role="spouse",
        )
        db.session.add(spouse)
        db.session.flush()
        _add_methods(spouse.id, _clean_email(row.get("spouse_email")), _clean(row.get("spouse_phone")))

    for event_key, header in usable_dates.items():
        event_date, year_known = parse_date(raw_row.get(header))
        if not event_date:
            continue
        db.session.add(TimelineEvent(
            contact_id=contact.id,
            event_type=event_key,
            event_date=event_date,
            year_known=year_known,
            # Imported milestones are recurring and belong on the
            # Important Dates card -- that is the entire reason an agent
            # brings a birthday column across.
            is_recurring=True,
            recurrence_rule="annual",
            is_important_date=True,
        ))
        if event_key == "birthday" and year_known:
            head.birthday = event_date

    for header, field in custom_fields.items():
        value = _coerce_custom_value(field, raw_row.get(header))
        if value is None:
            continue
        db.session.add(CustomFieldValue(
            contact_id=contact.id, field_definition_id=field.id, value=value,
        ))

    db.session.add(TimelineEvent(
        contact_id=contact.id,
        event_type="first_contact",
        event_date=datetime.utcnow().date(),
        is_recurring=False,
    ))
    return contact


def _add_methods(person_id, email, phone):
    if email:
        db.session.add(ContactMethod(
            person_id=person_id, method_type="email", subtype="personal",
            value=email, is_primary=True,
        ))
    if phone:
        db.session.add(ContactMethod(
            person_id=person_id, method_type="phone", subtype="mobile",
            value=phone, is_primary=True,
        ))
