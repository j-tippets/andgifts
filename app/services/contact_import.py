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
        self.status = status  # "created" | "duplicate" | "error" | "over_limit"
        self.household_name = household_name
        self.reason = reason

    def __repr__(self):
        return f"<RowResult line={self.line_number} {self.status} {self.reason or ''}>"


class ImportReport:
    def __init__(self):
        self.rows = []
        self.mapping = {}
        self.date_mapping = {}
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
    def errors(self):
        return [r for r in self.rows if r.status == "error"]

    @property
    def over_limit(self):
        return self._count("over_limit")

    def summary(self):
        return {
            "created": self.created,
            "duplicates": self.duplicates,
            "errors": len(self.errors),
            "over_limit": self.over_limit,
            "dry_run": self.dry_run,
        }


def _existing_emails_for_org(org_id):
    """Every email already known to this org, lowercased.

    One query rather than a lookup per row: a 400-row import would
    otherwise issue 400 queries, and this runs inside a request in the
    web version.
    """
    rows = (
        db.session.query(ContactMethod.value)
        .join(ContactPerson, ContactMethod.person_id == ContactPerson.id)
        .join(Contact, ContactPerson.contact_id == Contact.id)
        .filter(Contact.org_id == org_id, ContactMethod.method_type == "email")
        .all()
    )
    return {(value or "").strip().lower() for (value,) in rows if value}


def _existing_household_names_for_org(org_id):
    rows = db.session.query(Contact.household_name).filter(Contact.org_id == org_id).all()
    return {(name or "").strip().lower() for (name,) in rows if name}


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

    report.mapping = mapping
    report.date_mapping = usable_dates
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
            if head_email and head_email in known_emails:
                report.add(RowResult(index, "duplicate", household_name,
                                     reason=f"{head_email} is already in your contacts."))
                continue
            if not head_email and household_name.lower() in known_names:
                report.add(RowResult(index, "duplicate", household_name,
                                     reason=f"A contact named {household_name} already exists."))
                continue
            if duplicate_key in seen_in_file:
                report.add(RowResult(index, "duplicate", household_name,
                                     reason="Appears more than once in this file."))
                continue

            if limit is not None and current_count >= limit:
                report.add(RowResult(index, "over_limit", household_name,
                                     reason=f"Would exceed the plan's {limit}-contact limit."))
                continue

            _create_contact_from_row(row, raw_row, usable_dates, org, acting_user, owner_user)

            seen_in_file.add(duplicate_key)
            if head_email:
                known_emails.add(head_email)
            known_names.add(household_name.lower())
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


def _create_contact_from_row(row, raw_row, usable_dates, org, acting_user, owner_user):
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
