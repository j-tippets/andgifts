"""Error tracking wiring.

Everything the app does that can silently fail and cost real money --
a Stripe charge, a SendGrid send, a WDF handoff, a nightly job -- is
currently only observable by a customer telling us about it. That's the
gap this closes: an exception anywhere in a request or a scheduled job
becomes a notification instead of a mystery.

Deliberately structured so an unconfigured or broken Sentry can never
be the reason the app fails to boot:

  - No SENTRY_DSN  -> no-op. This is the default, so local dev and the
    test suite never emit events and nobody has to remember to opt out.
  - sentry_sdk not installed -> no-op. Keeps the dependency effectively
    optional for anyone running the app from a partial environment.
  - Anything else raises during init -> swallowed. Observability is
    supporting infrastructure; it doesn't get a veto over serving
    traffic.

`send_default_pii` is left at its default of False on purpose. Sentry
would otherwise attach request cookies, headers and form bodies to
every event, which for this app means session cookies, contact
addresses and payment-method form posts sitting in a third-party
system. Stack traces and the URL are what actually make an error
diagnosable; the PII is not worth the exposure.
"""
import logging

logger = logging.getLogger(__name__)


def init_sentry(app):
    """Initialises Sentry if configured. Returns True if it's live,
    False in every other case, so callers (and tests) can assert on
    the outcome rather than inferring it."""
    dsn = app.config.get("SENTRY_DSN")
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=app.config.get("SENTRY_TRACES_SAMPLE_RATE", 0.1),
            send_default_pii=False,
            # Ties every event to the exact deploy that produced it.
            # Reuses GIT_COMMIT_HASH, already set from DO App Platform's
            # ${_self.COMMIT_HASH} for asset cache-busting -- so a
            # regression can be traced to a commit without a separate
            # release-tagging step to keep in sync.
            release=app.config.get("STATIC_ASSET_VERSION"),
            environment=app.config.get("ENV_NAME", "production"),
        )
        return True
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry_sdk is not installed; error tracking is off")
        return False
    except Exception:
        logger.exception("Sentry initialisation failed; continuing without error tracking")
        return False
