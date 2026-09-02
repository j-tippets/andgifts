"""
Single place for the "are we in production" question, used by
fail-closed checks that must never quietly degrade in prod (missing
Stripe config, missing secrets, etc.). Deliberately keyed off
config.ENV_NAME rather than app.debug -- see config.py's
DevelopmentConfig/ProductionConfig comment for why those two are kept
independent.

Defaults to True (production) whenever ENV_NAME is missing or
unrecognized, so a misconfigured/unset environment fails closed
rather than accidentally getting dev-mode leniency.
"""
from flask import current_app


def is_production():
    return current_app.config.get("ENV_NAME") != "development"


def validate_secret_key(app):
    """Fails closed if SECRET_KEY is missing or still equal to the
    checked-in config.DEV_SECRET_KEY_DEFAULT, anywhere except real
    local development. Called once from create_app, right after
    config is loaded -- takes `app` directly rather than reusing
    is_production() above, since no app/request context exists yet
    for current_app to resolve at that point.

    Mirrors is_production()'s same "anything other than
    ENV_NAME == development needs the real thing" rule, so testing
    (which supplies its own real-looking TestingConfig.SECRET_KEY)
    passes without needing to special-case it here.

    A deploy running on a missing or default secret can forge session
    cookies (including login sessions) and CSRF tokens -- both are
    derived directly from SECRET_KEY -- since the default value is
    public: it's checked into this repo, in config.py. Refusing to
    start is safer than starting up quietly insecure."""
    from config import DEV_SECRET_KEY_DEFAULT

    if app.config.get("ENV_NAME") == "development":
        return

    secret_key = app.config.get("SECRET_KEY")
    if not secret_key or secret_key == DEV_SECRET_KEY_DEFAULT:
        raise RuntimeError(
            "SECRET_KEY is missing or still set to the checked-in development "
            "default ('dev-secret-change-me'). Set a real SECRET_KEY "
            "environment variable before starting outside of local "
            "development (ENV_NAME=development)."
        )
