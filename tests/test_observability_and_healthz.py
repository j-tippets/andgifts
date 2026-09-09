"""Covers the /healthz endpoint and Sentry's fail-open contract.

The point of testing init_sentry is not that Sentry works -- it's that
a missing, broken, or uninstalled Sentry can never take the app down.
Observability that can crash the thing it observes is a net negative,
and that property is easy to break later with a well-meaning
"shouldn't we fail loudly if the DSN is missing?" change.
"""
from app import create_app
from app.services.observability import init_sentry


def test_healthz_returns_ok_without_auth(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_healthz_reports_the_running_version(client, app):
    response = client.get("/healthz")

    assert response.get_json()["version"] == app.config["STATIC_ASSET_VERSION"]


def test_healthz_is_not_cached(client):
    # DO polls this to decide whether to restart the container. A cached
    # 200 from a process that has since wedged is the exact failure this
    # endpoint exists to catch.
    assert client.get("/healthz").headers["Cache-Control"] == "no-store"


def test_healthz_does_not_touch_the_database(client, app):
    """Guards the reasoning in the route's comment: a database blip must
    not fail the health check, because DO's response to a failed health
    check is to restart every container -- which cannot fix a database.

    Pointing SQLAlchemy at an unreachable URL would only prove the app
    doesn't connect lazily, so instead this asserts on the property
    directly: no statement is emitted while serving the request."""
    from sqlalchemy import event
    from app.extensions import db

    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get("/healthz").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements == [], f"/healthz issued queries: {statements}"


def test_sentry_is_disabled_when_dsn_is_unset(app):
    app.config["SENTRY_DSN"] = ""

    assert init_sentry(app) is False


def test_app_boots_with_a_malformed_sentry_dsn():
    """A bad DSN is a config typo, and a config typo must not be an
    outage. init_sentry swallows it and the factory still returns an
    app that serves traffic."""
    application = create_app("testing")
    application.config["SENTRY_DSN"] = "not-a-valid-dsn"

    assert init_sentry(application) is False

    with application.test_client() as client:
        assert client.get("/healthz").status_code == 200
