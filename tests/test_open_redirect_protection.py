"""
Regression tests for the open-redirect fix in app/routes/settings.py:
add_payment_method's next_url (a POST form field) and
add_payment_method_return's next (a GET query parameter) were both
used as redirect targets with no validation at all. The second one in
particular is directly exploitable with no login/session trickery
needed -- it's a plain GET endpoint, so a bare link like
/settings/payment-methods/added?next=https://evil.example.com sent to
a logged-in agent redirects them off-site the moment they click it.

Covers:
- is_safe_redirect_target directly: same-host paths and absolute URLs
  are accepted; a different host, a protocol-relative URL
  ("//evil.example.com", which browsers treat as "same scheme,
  different host" despite looking like a path), and a non-http(s)
  scheme are all rejected.
- add_payment_method_return falls back to the safe default instead of
  an attacker-supplied off-site `next`.
"""
from app.services.http_safety import is_safe_redirect_target

from tests.conftest import make_org_and_user
from tests.test_action_approval_idempotency import login_as


def test_relative_path_is_safe(app):
    with app.test_request_context("/settings/payment-methods"):
        assert is_safe_redirect_target("/orders/new") is True


def test_same_host_absolute_url_is_safe(app):
    with app.test_request_context("/settings/payment-methods"):
        assert is_safe_redirect_target("http://localhost/orders/new") is True


def test_empty_target_is_unsafe(app):
    with app.test_request_context("/settings/payment-methods"):
        assert is_safe_redirect_target("") is False
        assert is_safe_redirect_target(None) is False


def test_different_host_is_unsafe(app):
    with app.test_request_context("/settings/payment-methods"):
        assert is_safe_redirect_target("https://evil.example.com/phishing") is False


def test_protocol_relative_url_is_unsafe(app):
    """//evil.example.com looks like a path but browsers resolve it as
    same-scheme-different-host and follow it off-site -- the classic
    bypass for a naive `target.startswith("/")` check."""
    with app.test_request_context("/settings/payment-methods"):
        assert is_safe_redirect_target("//evil.example.com/phishing") is False


def test_non_http_scheme_is_unsafe(app):
    with app.test_request_context("/settings/payment-methods"):
        assert is_safe_redirect_target("javascript:alert(1)") is False


def test_add_payment_method_return_rejects_offsite_next(app, db, client):
    org, user = make_org_and_user(db)
    login_as(client, user)

    resp = client.get(
        "/settings/payment-methods/added",
        query_string={"next": "https://evil.example.com/phishing"},
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/settings/payment-methods", \
        "an off-site next must be replaced with the safe default, not followed"


def test_add_payment_method_return_accepts_onsite_next(app, db, client):
    org, user = make_org_and_user(db)
    login_as(client, user)

    resp = client.get(
        "/settings/payment-methods/added",
        query_string={"next": "/orders/new"},
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/orders/new"
