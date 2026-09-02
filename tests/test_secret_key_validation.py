"""
Regression tests for Priority 5 of the production-hardening review:
config.py fell back to a checked-in, publicly-known default SECRET_KEY
("dev-secret-change-me") whenever the SECRET_KEY environment variable
wasn't set -- fine for local development, but a real hole if it ever
happened outside development, since SECRET_KEY signs both session
cookies (login sessions) and CSRF tokens.

Covers all four required scenarios plus the testing config, exercised
directly against validate_secret_key() with a minimal Flask app
rather than through create_app("production") -- config.py's
class-level SECRET_KEY = os.environ.get(...) is evaluated once at
*import time*, so mutating os.environ inside a test can't actually
change what ProductionConfig.SECRET_KEY resolves to for that test
run. Testing the validation function directly against an app.config
built to represent each scenario tests the actual logic without
fighting that unrelated import-time quirk.
"""
import pytest
from flask import Flask

from app.services.environment import validate_secret_key
from config import DEV_SECRET_KEY_DEFAULT


def make_app(env_name, secret_key):
    app = Flask(__name__)
    app.config["ENV_NAME"] = env_name
    app.config["SECRET_KEY"] = secret_key
    return app


def test_development_missing_key_is_allowed():
    app = make_app("development", None)
    validate_secret_key(app)  # must not raise


def test_development_dev_default_is_allowed():
    app = make_app("development", DEV_SECRET_KEY_DEFAULT)
    validate_secret_key(app)  # must not raise -- this is the fallback's whole point


def test_testing_real_key_is_allowed():
    app = make_app("testing", "test-secret-key")
    validate_secret_key(app)  # must not raise


def test_production_missing_key_raises():
    app = make_app("production", None)
    with pytest.raises(RuntimeError):
        validate_secret_key(app)


def test_production_dev_default_raises():
    app = make_app("production", DEV_SECRET_KEY_DEFAULT)
    with pytest.raises(RuntimeError):
        validate_secret_key(app)


def test_production_real_secret_starts_normally():
    app = make_app("production", "a-genuinely-random-64-char-secret-value-not-the-dev-default")
    validate_secret_key(app)  # must not raise
