"""The web import flow: upload, review, commit.

The review step is the safety mechanism -- an agent importing 400
contacts cannot undo it, and there is no bulk delete to rescue them
with. So these tests care most about two things: that nothing is
written before the confirm button, and that a staged file (which is
client PII: names, home addresses, phone numbers, birthdays) is
reachable only by the org that uploaded it and doesn't outlive its
purpose.
"""
import io
from datetime import datetime, timedelta

from app.models import Contact, ContactImportJob
from app.services.suggestion_engine import purge_stale_contact_imports
from tests.conftest import make_org_and_user

CSV_BODY = (
    "First Name,Last Name,Email,City,State,Lead Source\n"
    "Jane,Rivera,jane@example.com,American Fork,UT,Zillow\n"
    "Sam,Okonkwo,sam@example.com,Provo,UT,Referral\n"
)


def _login(client, user):
    """Logs `client` in as `user`.

    The g.pop is load-bearing. The `app` fixture pushes ONE application
    context for the whole test and Flask-Login caches the resolved user
    on `g` (`g._login_user`), so without clearing it every request after
    the first is served as whoever logged in first -- regardless of the
    session cookie. In production each request gets a fresh app context
    and this cannot happen.

    That artifact silently defeats exactly the tests most worth having:
    a cross-org check written without this passes while actually
    re-running as the original user, so it proves nothing.
    """
    from flask import g

    g.pop("_login_user", None)
    with client.session_transaction() as session:
        session["_user_id"] = user.id
        session["_fresh"] = True


def _upload(client, body=CSV_BODY, filename="export.csv", assign_to_uploader=""):
    return client.post(
        "/contacts/import",
        data={
            "csv_file": (io.BytesIO(body.encode("utf-8")), filename),
            "assign_to_uploader": assign_to_uploader,
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


class TestUpload:
    def test_upload_stages_the_file_without_creating_contacts(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)

        response = _upload(client)

        assert response.status_code == 302
        assert ContactImportJob.query.filter_by(org_id=org.id).count() == 1
        assert Contact.query.filter_by(org_id=org.id).count() == 0

    def test_a_non_csv_file_is_refused(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)

        _upload(client, filename="contacts.pdf")

        assert ContactImportJob.query.count() == 0

    def test_an_empty_file_is_refused(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)

        _upload(client, body="   \n")

        assert ContactImportJob.query.count() == 0

    def test_an_excel_bom_does_not_break_column_matching(self, app, db, client):
        """Excel writes a BOM that would otherwise become part of the
        first header name, so "First Name" silently stops matching."""
        org, user = make_org_and_user(db)
        _login(client, user)

        client.post(
            "/contacts/import",
            data={"csv_file": (io.BytesIO(b"\xef\xbb\xbf" + CSV_BODY.encode("utf-8")), "excel.csv")},
            content_type="multipart/form-data",
        )
        job = ContactImportJob.query.one()
        response = client.get(f"/contacts/import/{job.id}")

        assert b"2 contacts ready to import" in response.data


class TestPreview:
    def test_preview_shows_the_counts_and_writes_nothing(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        response = client.get(f"/contacts/import/{job.id}")

        assert response.status_code == 200
        assert b"2 contacts ready to import" in response.data
        assert Contact.query.filter_by(org_id=org.id).count() == 0

    def test_preview_names_the_columns_it_is_skipping(self, app, db, client):
        """So the agent finds out their Lead Source column went nowhere
        now, rather than a week later."""
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        response = client.get(f"/contacts/import/{job.id}")

        assert b"Lead Source" in response.data

    def test_another_org_cannot_read_a_staged_file(self, app, db, client):
        """The staged CSV is a spreadsheet of someone's clients. This
        scoping is the only thing between orgs."""
        _org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        # A separate client, not the same one re-logged-in: swapping
        # users inside one session trips Flask-Login's session
        # protection and produces a redirect to /login, which would
        # make this test pass for the wrong reason.
        _other_org, other_user = make_org_and_user(db)
        other_client = app.test_client()
        _login(other_client, other_user)
        response = other_client.get(f"/contacts/import/{job.id}")

        assert response.status_code == 404

    def test_a_file_with_no_name_column_is_rejected_and_discarded(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client, body="Lead Source,Stage\nZillow,Hot\n")
        job = ContactImportJob.query.one()

        client.get(f"/contacts/import/{job.id}", follow_redirects=True)

        assert ContactImportJob.query.count() == 0, "an unusable file shouldn't linger"


class TestConfirm:
    def test_confirming_creates_the_contacts(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        client.post(f"/contacts/import/{job.id}/confirm", follow_redirects=True)

        assert Contact.query.filter_by(org_id=org.id).count() == 2

    def test_the_staged_file_is_deleted_after_import(self, app, db, client):
        """It's client PII and has served its purpose."""
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        client.post(f"/contacts/import/{job.id}/confirm", follow_redirects=True)

        assert ContactImportJob.query.count() == 0

    def test_the_ownership_choice_from_upload_is_honoured(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client, assign_to_uploader="1")
        job = ContactImportJob.query.one()

        client.post(f"/contacts/import/{job.id}/confirm", follow_redirects=True)

        contacts = Contact.query.filter_by(org_id=org.id).all()
        assert all(c.owner_user_id == user.id for c in contacts)

    def test_shared_is_honoured_too(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client, assign_to_uploader="")
        job = ContactImportJob.query.one()

        client.post(f"/contacts/import/{job.id}/confirm", follow_redirects=True)

        contacts = Contact.query.filter_by(org_id=org.id).all()
        assert all(c.owner_user_id is None for c in contacts)

    def test_another_org_cannot_confirm_someone_elses_import(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        _other_org, other_user = make_org_and_user(db)
        other_client = app.test_client()
        _login(other_client, other_user)
        response = other_client.post(f"/contacts/import/{job.id}/confirm")

        assert response.status_code == 404
        assert Contact.query.count() == 0

    def test_confirming_twice_does_not_duplicate(self, app, db, client):
        """The job is deleted on confirm, so a double-submit or a
        back-button retry 404s rather than importing again."""
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        client.post(f"/contacts/import/{job.id}/confirm", follow_redirects=True)
        second = client.post(f"/contacts/import/{job.id}/confirm")

        assert second.status_code == 404
        assert Contact.query.filter_by(org_id=org.id).count() == 2


class TestCancelAndCleanup:
    def test_cancelling_discards_the_file(self, app, db, client):
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()

        client.post(f"/contacts/import/{job.id}/cancel", follow_redirects=True)

        assert ContactImportJob.query.count() == 0
        assert Contact.query.filter_by(org_id=org.id).count() == 0

    def test_abandoned_uploads_are_swept(self, app, db, client):
        """A closed tab shouldn't leave a spreadsheet of someone's
        clients in the database indefinitely."""
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)
        job = ContactImportJob.query.one()
        job.created_at = datetime.utcnow() - timedelta(hours=30)
        db.session.commit()

        purged = purge_stale_contact_imports(stale_after_hours=24)

        assert len(purged) == 1
        assert ContactImportJob.query.count() == 0

    def test_a_recent_upload_is_left_alone(self, app, db, client):
        """Must never delete a file someone is still reviewing."""
        org, user = make_org_and_user(db)
        _login(client, user)
        _upload(client)

        assert purge_stale_contact_imports(stale_after_hours=24) == []
        assert ContactImportJob.query.count() == 1
