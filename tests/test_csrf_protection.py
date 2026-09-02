"""
Regression tests for Priority 4 of the production-hardening review:
the app had no global CSRF protection at all (Flask-WTF's CSRFProtect
was never installed), despite many authenticated POST endpoints
performing sensitive actions (approve, skip, delete, edit, etc.).

These tests deliberately build their own app instance with
WTF_CSRF_ENABLED forced back on -- the shared `app` fixture in
conftest.py runs with it off (TestingConfig's default, so the rest of
the suite doesn't need to thread tokens through every POST), which is
exactly why CSRF enforcement itself needs its own dedicated coverage
here rather than relying on that fixture.

Covers:
- An authenticated POST with no CSRF token is rejected.
- A normal form POST that DOES include a valid token succeeds.
- An AJAX POST with a valid token in the X-CSRFToken header succeeds.
- The Stripe webhook keeps working with no CSRF token at all (it's
  explicitly exempted, verified by Stripe's own signature instead).
"""
import pytest
from datetime import date, timedelta

from app import create_app
from app.extensions import db as _db
from app.models import Contact, SuggestedAction

from tests.conftest import make_org_and_user


@pytest.fixture()
def csrf_app():
    """Same as conftest's `app` fixture, except WTF_CSRF_ENABLED is
    forced back on -- everything else in the suite wants it off, but
    these tests exist specifically to prove enforcement works."""
    application = create_app("testing")
    application.config["WTF_CSRF_ENABLED"] = True
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def csrf_client(csrf_app):
    return csrf_app.test_client()


def make_login_and_action(csrf_app, csrf_client):
    """Builds an org/user/contact/pending-email-action, logs the user
    into csrf_client's session, and returns just the action_id -- IDs
    only, never the ORM objects themselves, since those become
    detached once the app_context that created them exits (the
    session backing them is torn down), and every test here needs to
    make requests -- outside any app_context -- after this setup."""
    with csrf_app.app_context():
        org, user = make_org_and_user(_db)
        contact = Contact(org_id=org.id, owner_user_id=user.id, household_name="The CSRFs")
        _db.session.add(contact)
        _db.session.flush()

        action = SuggestedAction(
            org_id=org.id,
            contact_id=contact.id,
            source_campaign_id=None,
            action_type="email",
            reason_text="Test reason",
            generated_message="Hi there",
            target_date=date.today() + timedelta(days=1),
            status="pending",
        )
        _db.session.add(action)
        _db.session.commit()
        user_id, action_id = user.id, action.id

    with csrf_client.session_transaction() as sess:
        sess["_user_id"] = user_id
        sess["_fresh"] = True

    return action_id


def get_valid_csrf_token(app, client):
    """Manufactures a CSRF token that validates against `client`'s
    actual session. generate_csrf() itself needs an active request
    context to read/write the flask.session proxy, but
    session_transaction() deliberately does NOT leave one active
    during its yielded block (it pushes a request context only
    briefly, to open the session, before yielding -- see its
    docstring/source) -- so this replicates generate_csrf()'s two
    steps by hand instead: seed the session's csrf_token field
    directly (no request context needed, `sess` is a plain dict-like
    object), then sign that seed exactly the way
    flask_wtf.csrf.generate_csrf does."""
    import hashlib
    import os
    from itsdangerous import URLSafeTimedSerializer

    seed = hashlib.sha1(os.urandom(64)).hexdigest()
    with client.session_transaction() as sess:
        sess["csrf_token"] = seed

    with app.app_context():
        secret_key = app.config.get("WTF_CSRF_SECRET_KEY") or app.secret_key
        serializer = URLSafeTimedSerializer(secret_key, salt="wtf-csrf-token")
        return serializer.dumps(seed)


def action_status(csrf_app, action_id):
    with csrf_app.app_context():
        return SuggestedAction.query.get(action_id).status


def test_authenticated_post_without_csrf_token_fails(csrf_app, csrf_client):
    action_id = make_login_and_action(csrf_app, csrf_client)

    resp = csrf_client.post(f"/dashboard/actions/{action_id}/skip", data={})

    assert resp.status_code == 400
    assert action_status(csrf_app, action_id) == "pending", \
        "a rejected CSRF request must not have any side effect"


def test_valid_form_post_succeeds(csrf_app, csrf_client):
    action_id = make_login_and_action(csrf_app, csrf_client)
    token = get_valid_csrf_token(csrf_app, csrf_client)

    resp = csrf_client.post(
        f"/dashboard/actions/{action_id}/skip",
        data={"csrf_token": token},
    )

    assert resp.status_code == 302
    assert action_status(csrf_app, action_id) == "skipped"


def test_ajax_post_with_valid_csrf_header_succeeds(csrf_app, csrf_client):
    action_id = make_login_and_action(csrf_app, csrf_client)
    token = get_valid_csrf_token(csrf_app, csrf_client)

    resp = csrf_client.post(
        f"/dashboard/actions/{action_id}/skip",
        headers={"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": token},
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert action_status(csrf_app, action_id) == "skipped"


def test_stripe_webhook_exempt_from_csrf(csrf_app, csrf_client):
    """No CSRF token at all -- must not be rejected FOR THAT REASON.
    STRIPE_SECRET_KEY isn't configured in the testing config, so the
    view itself bails with 503 before doing anything else; the point
    here is that it reaches that view logic at all instead of getting
    a CSRF-layer 400 first."""
    resp = csrf_client.post("/webhooks/stripe", data=b"{}", content_type="application/json")
    assert resp.status_code == 503
