"""
Regression tests for Priority 6 of the production-hardening review:
MIN_PASSWORD_LENGTH went from 8 to 12, but the constant was only ever
checked in profile.py's change-password flow -- signup (onboarding),
password reset (auth), invite acceptance (team), and an admin-set
temp password (team.new_member) all set a password with zero length
validation at all. Bumping the constant without enforcing it
everywhere a password gets set would have been mostly theater, so
this covers all five call sites.
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import User

from tests.conftest import make_org_and_user


SHORT_PASSWORD = "short1"  # 6 chars, well under the 12-char minimum
LONG_PASSWORD = "a genuinely long password"  # well over 12


def test_signup_rejects_short_password(client, db):
    resp = client.post("/get-started/", data={
        "email": "newuser@example.com",
        "org_name": "New Org",
        "first_name": "New",
        "last_name": "User",
        "password": SHORT_PASSWORD,
    })
    assert resp.status_code == 302
    assert User.query.filter_by(email="newuser@example.com").first() is None, \
        "no account should be created when the password is too short"


def test_signup_accepts_long_password(client, db):
    resp = client.post("/get-started/", data={
        "email": "newuser2@example.com",
        "org_name": "New Org 2",
        "first_name": "New",
        "last_name": "User",
        "password": LONG_PASSWORD,
    })
    assert resp.status_code == 302
    user = User.query.filter_by(email="newuser2@example.com").first()
    assert user is not None
    assert user.check_password(LONG_PASSWORD)


def test_password_reset_rejects_short_password(client, db):
    org, user = make_org_and_user(db)
    user.reset_token = "reset-token-123"
    user.reset_expires_at = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()
    old_hash = user.password_hash

    resp = client.post(
        "/auth/reset-password/reset-token-123",
        data={"password": SHORT_PASSWORD, "confirm_password": SHORT_PASSWORD},
    )

    assert resp.status_code == 200  # re-renders the form with an error, no redirect
    refreshed = User.query.get(user.id)
    assert refreshed.password_hash == old_hash, "a rejected reset must not change the password"


def test_password_reset_accepts_long_password(client, db):
    org, user = make_org_and_user(db)
    user.reset_token = "reset-token-456"
    user.reset_expires_at = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()

    resp = client.post(
        "/auth/reset-password/reset-token-456",
        data={"password": LONG_PASSWORD, "confirm_password": LONG_PASSWORD},
    )

    assert resp.status_code == 302
    refreshed = User.query.get(user.id)
    assert refreshed.check_password(LONG_PASSWORD)


def test_invite_accept_rejects_short_password(client, db):
    org, admin = make_org_and_user(db)
    invitee = User(
        org_id=org.id, email="invitee@example.com", first_name="In", last_name="Vitee",
        role="agent", status="pending",
        invite_token="invite-token-123", invite_expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.session.add(invitee)
    db.session.commit()

    resp = client.post(
        "/team/accept/invite-token-123",
        data={"password": SHORT_PASSWORD, "confirm_password": SHORT_PASSWORD},
    )

    assert resp.status_code == 200
    refreshed = User.query.get(invitee.id)
    assert refreshed.status == "pending", "a rejected invite acceptance must not activate the account"


def test_invite_accept_accepts_long_password(client, db):
    org, admin = make_org_and_user(db)
    invitee = User(
        org_id=org.id, email="invitee2@example.com", first_name="In", last_name="Vitee",
        role="agent", status="pending",
        invite_token="invite-token-456", invite_expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.session.add(invitee)
    db.session.commit()

    resp = client.post(
        "/team/accept/invite-token-456",
        data={"password": LONG_PASSWORD, "confirm_password": LONG_PASSWORD},
    )

    assert resp.status_code == 302
    refreshed = User.query.get(invitee.id)
    assert refreshed.status == "active"
    assert refreshed.check_password(LONG_PASSWORD)


def test_admin_set_temp_password_rejects_short_password(client, db):
    org, admin = make_org_and_user(db, tier="pro")  # free tier's 1-seat cap can't add a member
    with client.session_transaction() as sess:
        sess["_user_id"] = admin.id
        sess["_fresh"] = True

    resp = client.post("/team/new", data={
        "email": "directhire@example.com",
        "first_name": "Direct",
        "last_name": "Hire",
        "method": "direct",
        "temp_password": SHORT_PASSWORD,
    })

    assert resp.status_code == 302
    assert User.query.filter_by(email="directhire@example.com").first() is None, \
        "no account should be created when the admin-set temp password is too short"


def test_admin_set_temp_password_accepts_long_password(client, db):
    org, admin = make_org_and_user(db, tier="pro")
    with client.session_transaction() as sess:
        sess["_user_id"] = admin.id
        sess["_fresh"] = True

    resp = client.post("/team/new", data={
        "email": "directhire2@example.com",
        "first_name": "Direct",
        "last_name": "Hire",
        "method": "direct",
        "temp_password": LONG_PASSWORD,
    })

    assert resp.status_code == 302
    new_user = User.query.filter_by(email="directhire2@example.com").first()
    assert new_user is not None
    assert new_user.check_password(LONG_PASSWORD)
