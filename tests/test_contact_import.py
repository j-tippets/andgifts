"""CSV contact import.

The failure mode that matters here isn't a crash -- it's an import that
appears to work and quietly mangles 400 records. An agent can fix a
spreadsheet and re-run; they cannot fix data they were never told was
wrong. So most of these tests are about what gets *reported* rather
than what gets created.
"""
import pytest

from app.models import (
    Contact,
    ContactMethod,
    ContactPerson,
    CustomEventType,
    CustomFieldDefinition,
    CustomFieldValue,
    TimelineEvent,
    User,
)
from app.services.contact_import import (
    detect_columns,
    import_contacts,
    parse_date,
)
from tests.conftest import make_org_and_user


FOLLOW_UP_BOSS = (
    "First Name,Last Name,Email,Phone,Address,City,State,Zip,Birthday\n"
    "Jane,Rivera,jane@example.com,555-0100,1 Main St,American Fork,ut,84003,1985-04-12\n"
    "Sam,Okonkwo,sam@example.com,555-0101,2 Oak Ave,Provo,UT,84601,03/09\n"
)


FOLLOW_UP_BOSS_WITH_SOURCE = (
    "First Name,Last Name,Email,Lead Source\n"
    "Jane,Rivera,jane@example.com,Zillow\n"
    "Sam,Okonkwo,sam@example.com,Referral\n"
)


def _import(db, org, user, csv_text, **kwargs):
    return import_contacts(csv_text, org, user, **kwargs)


class TestColumnDetection:
    def test_recognises_a_follow_up_boss_export(self):
        mapping, dates, unmapped = detect_columns(
            ["First Name", "Last Name", "Email", "Phone", "Address", "City", "State", "Zip"]
        )

        assert mapping["head_first_name"] == "First Name"
        assert mapping["head_email"] == "Email"
        assert mapping["shipping_city"] == "City"
        assert unmapped == []

    def test_header_style_does_not_matter(self):
        mapping, _dates, _unmapped = detect_columns(["first_name", "LASTNAME", "E-Mail Address"])

        assert mapping["head_first_name"] == "first_name"
        assert mapping["head_last_name"] == "LASTNAME"
        assert mapping["head_email"] == "E-Mail Address"

    def test_a_header_is_never_claimed_twice(self):
        """"Last Name" is a plausible alias for both household_name and
        head_last_name. Letting one column populate two fields produces
        contacts that look right and are subtly wrong."""
        mapping, _dates, _unmapped = detect_columns(["First Name", "Last Name"])

        assert mapping["head_last_name"] == "Last Name"
        assert "household_name" not in mapping

    def test_unknown_columns_are_reported_not_dropped(self):
        """The agent needs to know their 'Lead Source' column went
        nowhere."""
        _mapping, _dates, unmapped = detect_columns(["First Name", "Lead Source", "Pipeline Stage"])

        assert "Lead Source" in unmapped
        assert "Pipeline Stage" in unmapped


class TestDateParsing:
    @pytest.mark.parametrize("raw", ["1985-04-12", "04/12/1985", "April 12, 1985", "1985/04/12"])
    def test_common_formats(self, raw):
        parsed, year_known = parse_date(raw)

        assert (parsed.month, parsed.day) == (4, 12)
        assert year_known is True

    def test_month_and_day_without_a_year(self):
        """Extremely common in CRM exports, and dropping it would lose
        the birthday entirely -- which is the whole reason the column
        was brought across."""
        parsed, year_known = parse_date("03/09")

        assert (parsed.month, parsed.day) == (3, 9)
        assert year_known is False

    def test_leap_day_survives_a_missing_year(self):
        parsed, year_known = parse_date("02/29")

        assert (parsed.month, parsed.day) == (2, 29)
        assert year_known is False

    def test_unparseable_values_are_refused_not_guessed(self):
        assert parse_date("sometime in spring") == (None, None)
        assert parse_date("") == (None, None)


class TestImport:
    def test_a_dry_run_writes_nothing(self, app, db):
        org, user = make_org_and_user(db)

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=True)

        assert report.created == 2
        assert Contact.query.filter_by(org_id=org.id).count() == 0

    def test_committing_creates_the_contacts(self, app, db):
        org, user = make_org_and_user(db)

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        assert report.created == 2
        assert Contact.query.filter_by(org_id=org.id).count() == 2

    def test_household_name_is_derived_from_the_surname(self, app, db):
        org, user = make_org_and_user(db)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        names = {c.household_name for c in Contact.query.filter_by(org_id=org.id)}
        assert names == {"The Riveras", "The Okonkwos"}

    def test_people_and_contact_methods_are_created(self, app, db):
        org, user = make_org_and_user(db)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        head = ContactPerson.query.filter_by(contact_id=contact.id).one()
        assert (head.first_name, head.last_name) == ("Jane", "Rivera")

        methods = {m.method_type: m.value for m in
                   ContactMethod.query.filter_by(person_id=head.id)}
        assert methods["email"] == "jane@example.com"
        assert methods["phone"] == "555-0100"

    def test_address_is_imported_and_state_normalised(self, app, db):
        org, user = make_org_and_user(db)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert contact.shipping_city == "American Fork"
        assert contact.shipping_state == "UT", "lowercase 'ut' should be normalised"
        assert contact.has_shipping_address

    def test_imported_contacts_get_a_first_contact_event(self, app, db):
        """new_contact seeds this, and the suggestion engine and contact
        page both assume it exists. An imported contact has to behave
        identically to a hand-entered one."""
        org, user = make_org_and_user(db)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert TimelineEvent.query.filter_by(
            contact_id=contact.id, event_type="first_contact"
        ).count() == 1

    def test_contacts_can_be_made_private_to_an_agent(self, app, db):
        org, user = make_org_and_user(db)

        _import(db, org, user, FOLLOW_UP_BOSS, owner_user=user, dry_run=False)

        assert all(c.owner_user_id == user.id for c in Contact.query.filter_by(org_id=org.id))

    def test_contacts_are_shared_when_no_owner_is_given(self, app, db):
        org, user = make_org_and_user(db)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        assert all(c.owner_user_id is None for c in Contact.query.filter_by(org_id=org.id))


class TestMilestones:
    def _with_birthday_type(self, db, org):
        db.session.add(CustomEventType(
            org_id=org.id, key="birthday", label="Birthday", scope="org",
        ))
        db.session.commit()

    def test_a_birthday_column_becomes_a_recurring_important_date(self, app, db):
        org, user = make_org_and_user(db)
        self._with_birthday_type(db, org)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        event = TimelineEvent.query.filter_by(contact_id=contact.id, event_type="birthday").one()
        assert event.event_date.month == 4
        assert event.is_recurring is True
        assert event.is_important_date is True

    def test_a_yearless_birthday_is_flagged_not_dropped(self, app, db):
        org, user = make_org_and_user(db)
        self._with_birthday_type(db, org)

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Okonkwos").one()
        event = TimelineEvent.query.filter_by(contact_id=contact.id, event_type="birthday").one()
        assert (event.event_date.month, event.event_date.day) == (3, 9)
        assert event.year_known is False

    def test_an_import_never_invents_event_types(self, app, db):
        """Creating a CustomEventType from a spreadsheet header would let
        a CSV silently reconfigure the org. The column is reported as
        ignored instead."""
        org, user = make_org_and_user(db)  # no birthday event type

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        assert CustomEventType.query.filter_by(org_id=org.id, key="birthday").count() == 0
        assert "Birthday" in report.unmapped_headers
        assert TimelineEvent.query.filter_by(event_type="birthday").count() == 0


class TestDeduplication:
    def test_an_existing_email_is_skipped(self, app, db):
        org, user = make_org_and_user(db)
        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        assert report.created == 0
        assert report.duplicates == 2
        assert Contact.query.filter_by(org_id=org.id).count() == 2

    def test_repeats_within_one_file_are_caught(self, app, db):
        org, user = make_org_and_user(db)
        csv_text = (
            "First Name,Last Name,Email\n"
            "Jane,Rivera,jane@example.com\n"
            "Jane,Rivera,JANE@EXAMPLE.COM\n"
        )

        report = _import(db, org, user, csv_text, dry_run=False)

        assert report.created == 1
        assert report.duplicates == 1, "matching should be case-insensitive"

    def test_another_orgs_contacts_are_not_treated_as_duplicates(self, app, db):
        """Dedupe is per-org. Two agencies both having a jane@example.com
        is normal and must not block the second one's import."""
        org, user = make_org_and_user(db)
        other_org, other_user = make_org_and_user(db)
        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        report = _import(db, other_org, other_user, FOLLOW_UP_BOSS, dry_run=False)

        assert report.created == 2

    def test_name_matching_is_used_only_without_an_email(self, app, db):
        org, user = make_org_and_user(db)
        _import(db, org, user, "First Name,Last Name\nJane,Rivera\n", dry_run=False)

        report = _import(db, org, user, "First Name,Last Name\nJane,Rivera\n", dry_run=False)

        assert report.duplicates == 1


class TestBadData:
    def test_a_row_with_no_name_is_an_error_not_a_contact(self, app, db):
        """"Unknown Unknown" in a contact list is worse than a rejected
        row -- the agent can fix the second and won't notice the first."""
        org, user = make_org_and_user(db)
        csv_text = "First Name,Last Name,Email\n,,nobody@example.com\n"

        report = _import(db, org, user, csv_text, dry_run=False)

        assert report.created == 0
        assert len(report.errors) == 1
        assert report.errors[0].line_number == 2

    def test_a_malformed_email_is_reported_with_its_line_number(self, app, db):
        org, user = make_org_and_user(db)
        csv_text = "First Name,Last Name,Email\nJane,Rivera,not-an-email\n"

        report = _import(db, org, user, csv_text, dry_run=False)

        assert report.created == 0
        assert report.errors[0].line_number == 2
        assert "not-an-email" in report.errors[0].reason

    def test_good_rows_still_import_alongside_bad_ones(self, app, db):
        """All-or-nothing would mean one typo in row 300 costs the agent
        the entire import."""
        org, user = make_org_and_user(db)
        csv_text = (
            "First Name,Last Name,Email\n"
            "Jane,Rivera,jane@example.com\n"
            ",,\n"
            "Sam,Okonkwo,sam@example.com\n"
        )

        report = _import(db, org, user, csv_text, dry_run=False)

        assert report.created == 2
        assert len(report.errors) == 1

    def test_a_file_with_no_recognisable_name_column_is_refused(self, app, db):
        org, user = make_org_and_user(db)

        with pytest.raises(ValueError, match="name column"):
            _import(db, org, user, "Lead Source,Pipeline\nZillow,Hot\n", dry_run=False)

    def test_an_empty_file_is_refused(self, app, db):
        org, user = make_org_and_user(db)

        with pytest.raises(ValueError):
            _import(db, org, user, "", dry_run=False)


class TestPlanLimits:
    def test_the_import_stops_at_the_plan_limit(self, app, db, monkeypatch):
        org, user = make_org_and_user(db)
        monkeypatch.setattr(type(org), "limit_for", lambda self, key: 1)

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        assert report.created == 1
        assert report.over_limit == 1
        assert Contact.query.filter_by(org_id=org.id).count() == 1

    def test_the_blocked_rows_are_named(self, app, db, monkeypatch):
        """So the agent knows which contacts didn't come across, rather
        than just that some didn't."""
        org, user = make_org_and_user(db)
        monkeypatch.setattr(type(org), "limit_for", lambda self, key: 1)

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        blocked = [r for r in report.rows if r.status == "over_limit"]
        assert blocked[0].household_name == "The Okonkwos"


class TestCustomFields:
    """A CSV column can match a Milestone, a Custom Field, or both.

    The distinction is real and easy to get wrong: a Milestone is what
    gift suggestions and flows trigger on; a Custom Field is inert data
    on the record. An agency may have set up either, both, or neither
    for the same concept, so a column matching both must populate both
    rather than the import quietly choosing one.
    """

    def _field(self, db, org, label, field_type="text", options=None, owner=None):
        field = CustomFieldDefinition(
            org_id=org.id, label=label, field_type=field_type, options=options,
            scope="personal" if owner else "org",
            owner_user_id=owner.id if owner else None,
        )
        db.session.add(field)
        db.session.commit()
        return field

    def _value(self, db, contact, field):
        return CustomFieldValue.query.filter_by(
            contact_id=contact.id, field_definition_id=field.id
        ).one_or_none()

    def test_a_column_matching_a_custom_field_is_imported(self, app, db):
        org, user = make_org_and_user(db)
        field = self._field(db, org, "Lead Source")

        report = _import(db, org, user, FOLLOW_UP_BOSS_WITH_SOURCE, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert self._value(db, contact, field).value == "Zillow"
        assert "Lead Source" not in report.unmapped_headers

    def test_one_column_can_feed_both_a_milestone_and_a_custom_field(self, app, db):
        """The case that prompted this: an agency had added Birthday as
        a custom field, so the milestone path silently did nothing."""
        org, user = make_org_and_user(db)
        db.session.add(CustomEventType(org_id=org.id, key="birthday", label="Birthday", scope="org"))
        field = self._field(db, org, "Birthday", field_type="date")
        db.session.commit()

        _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert TimelineEvent.query.filter_by(
            contact_id=contact.id, event_type="birthday"
        ).count() == 1, "should still create the milestone"
        assert self._value(db, contact, field).value == "1985-04-12", "and the custom field"

    def test_a_custom_field_alone_still_works(self, app, db):
        """No milestone configured -- the column should stop being
        reported as skipped and land on the record instead."""
        org, user = make_org_and_user(db)
        field = self._field(db, org, "Birthday", field_type="date")

        report = _import(db, org, user, FOLLOW_UP_BOSS, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert self._value(db, contact, field).value == "1985-04-12"
        assert "Birthday" not in report.unmapped_headers

    def test_matching_ignores_case_and_punctuation(self, app, db):
        org, user = make_org_and_user(db)
        field = self._field(db, org, "lead source")

        _import(db, org, user, FOLLOW_UP_BOSS_WITH_SOURCE, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert self._value(db, contact, field) is not None

    def test_another_agents_personal_field_is_not_used(self, app, db):
        """Personal fields are private. An import run by one agent must
        not write into another agent's field."""
        org, user = make_org_and_user(db)
        other = User(org_id=org.id, email="other@example.com", first_name="Other",
                     last_name="Agent", role="agent", email_verified=True)
        other.set_password("correct horse battery staple")
        db.session.add(other)
        db.session.commit()
        field = self._field(db, org, "Lead Source", owner=other)

        report = _import(db, org, user, FOLLOW_UP_BOSS_WITH_SOURCE, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert self._value(db, contact, field) is None
        assert "Lead Source" in report.unmapped_headers

    def test_a_select_value_outside_the_options_is_not_stored(self, app, db):
        """Storing a value the edit dropdown can't represent would leave
        the agent unable to see or fix it."""
        org, user = make_org_and_user(db)
        field = self._field(db, org, "Lead Source", field_type="select", options="Referral,Open House")

        _import(db, org, user, FOLLOW_UP_BOSS_WITH_SOURCE, dry_run=False)

        rivera = Contact.query.filter_by(household_name="The Riveras").one()   # Zillow
        okonkwo = Contact.query.filter_by(household_name="The Okonkwos").one() # Referral
        assert self._value(db, rivera, field) is None
        assert self._value(db, okonkwo, field).value == "Referral"

    def test_currency_values_are_stripped_of_formatting(self, app, db):
        org, user = make_org_and_user(db)
        field = self._field(db, org, "Income", field_type="currency")
        csv_text = "First Name,Last Name,Email,Income\nJane,Rivera,jane@example.com,\"$125,000\"\n"

        _import(db, org, user, csv_text, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert self._value(db, contact, field).value == "125000"

    def test_an_empty_cell_writes_no_value(self, app, db):
        org, user = make_org_and_user(db)
        field = self._field(db, org, "Lead Source")
        csv_text = "First Name,Last Name,Email,Lead Source\nJane,Rivera,jane@example.com,\n"

        _import(db, org, user, csv_text, dry_run=False)

        contact = Contact.query.filter_by(household_name="The Riveras").one()
        assert self._value(db, contact, field) is None
