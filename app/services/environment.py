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
