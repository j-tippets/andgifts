"""
Thin wrapper around the Stripe SDK. Centralizes api_key configuration so
routes never touch the key directly. Returns None (rather than raising)
when Stripe isn't configured -- callers are expected to flash a friendly
error and bail rather than 500.
"""
from flask import current_app


def get_stripe():
    api_key = current_app.config.get("STRIPE_SECRET_KEY")
    if not api_key:
        return None
    try:
        import stripe
        stripe.api_key = api_key
        return stripe
    except Exception:
        return None
