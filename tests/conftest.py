import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import create_app
from app.extensions import db as _db
from app.models import Org, User


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ships with foreign key enforcement OFF by default and has
    to be told, per connection, to turn it on. Production is
    MySQL/InnoDB, which enforces FKs unconditionally -- so without this
    the test suite is running against materially different constraint
    semantics than the thing it's supposed to be testing.

    That gap is not theoretical: every deletion path in the app
    (delete_member, delete_account, delete_org_completely,
    delete_contact, library_delete) clears *some* of the references
    pointing at the row being deleted and misses others. Under SQLite
    with FKs off, the orphaned references are silently accepted and the
    tests pass. Under MySQL they raise IntegrityError and the user gets
    a 500. The suite could never have caught it.

    Guarded to sqlite3 connections specifically: PRAGMA is SQLite
    syntax, and this listener is registered on the base Engine class,
    so it fires for any engine the process opens.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture()
def app():
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    return _db


def make_org_and_user(db, tier="free", onboarding_step="plan", **org_kwargs):
    """Builds an Org + its owner User the way onboarding.start() does,
    skipping the HTTP hop so tests can drop straight into whichever
    wizard step they're exercising."""
    org = Org(name="Test Agency", tier=tier, onboarding_step=onboarding_step, **org_kwargs)
    db.session.add(org)
    db.session.flush()
    org.sender_local_part = Org.generate_sender_local_part(org.name)

    user = User(
        org_id=org.id,
        email="owner@example.com",
        first_name="Owner",
        last_name="Test",
        role="admin",
        email_verified=True,
    )
    user.set_password("correct horse battery staple")
    db.session.add(user)
    db.session.commit()
    return org, user


@pytest.fixture()
def wizard_session(client):
    """Sets session["onboarding"] the way onboarding.start() does, so
    subsequent requests through `client` are treated as mid-wizard for
    the given org/user."""
    def _set(org, user):
        with client.session_transaction() as sess:
            sess["onboarding"] = {"org_id": org.id, "user_id": user.id}
    return _set
