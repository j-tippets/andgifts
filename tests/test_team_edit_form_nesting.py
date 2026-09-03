"""
Regression test for a real bug found in QA: team/edit.html's
Disable/Reactivate forms were nested inside the main profile-edit
<form> (both opened by the same un-hide edit that first surfaced them
in the UI). Nested <form> elements are invalid HTML -- browsers
silently discard the inner <form> tag but leave its child inputs
behind as part of the still-open outer form, so clicking "Disable"
actually submitted the outer edit form (to /team/<id>/edit) instead
of the disable endpoint. Confirmed in the wild: the browser network
tab showed a request to the edit endpoint with the csrf_token field
duplicated (one from each form), and the member's status never
changed.

This isn't the kind of bug a route-level test catches -- the routes
themselves were always correct, hitting them directly bypasses the
browser's form-nesting-collapse behavior entirely. This test parses
the actual rendered HTML and fails if the structural mistake exists,
regardless of the routes underneath.
"""
from html.parser import HTMLParser

from app.models import Contact, User

from tests.conftest import make_org_and_user
from tests.test_action_approval_idempotency import login_as


class _FormNestingChecker(HTMLParser):
    """Tracks <form> nesting depth; records the id of any <form> that
    opens while another is still open. Browsers collapse this in a way
    that silently breaks whichever inner form(s) exist -- see this
    file's module docstring."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested_form_ids = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            if self.depth > 0:
                self.nested_form_ids.append(dict(attrs).get("id", "(no id)"))
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth = max(0, self.depth - 1)


def assert_no_nested_forms(html):
    checker = _FormNestingChecker()
    checker.feed(html)
    assert not checker.nested_form_ids, (
        f"Nested <form> tag(s) found: {checker.nested_form_ids} -- browsers silently "
        "break these (the inner form's inputs get absorbed into the still-open outer "
        "form instead of submitting to their own action)."
    )


def test_team_edit_page_has_no_nested_forms(app, db, client):
    org, admin = make_org_and_user(db)
    member = User(
        org_id=org.id, email="teammate@example.com", first_name="Team", last_name="Mate",
        role="agent", status="active",
    )
    db.session.add(member)
    db.session.commit()
    login_as(client, admin)

    resp = client.get(f"/team/{member.id}/edit")
    assert resp.status_code == 200
    assert_no_nested_forms(resp.get_data(as_text=True))


def test_disable_button_actually_disables_not_silently_resave_profile(app, db, client):
    """The end-to-end symptom: clicking Disable must change status, not
    silently re-save the profile with its existing values (which is
    what happened when the disable form was nested and got absorbed
    into the outer edit form's submission)."""
    org, admin = make_org_and_user(db)
    member = User(
        org_id=org.id, email="teammate2@example.com", first_name="Team", last_name="Mate",
        role="agent", status="active",
    )
    db.session.add(member)
    db.session.commit()
    member_id = member.id
    login_as(client, admin)

    resp = client.post(f"/team/{member_id}/disable")

    assert resp.status_code == 302
    refreshed = User.query.get(member_id)
    assert refreshed.status == "disabled"
