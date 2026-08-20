import os
from datetime import timedelta


class Config:
    """
    Base config. All values pulled from environment variables so this runs
    identically on local dev and DigitalOcean App Platform (which injects
    env vars from the managed MySQL database + app-level secrets).
    """

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # --- Database ---
    # DigitalOcean managed MySQL gives you individual components; we build
    # the SQLAlchemy URI from them so nothing sensitive is hardcoded.
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "25060")  # DO managed MySQL default
    DB_NAME = os.environ.get("DB_NAME", "ag_crm")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        os.environ.get("LOCAL_SQLITE_URI")
        or f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,   # avoids stale-connection errors on managed DBs
        "pool_recycle": 280,
    }
    if SQLALCHEMY_DATABASE_URI.startswith("mysql"):
        # DO managed MySQL requires SSL. PyMySQL (not mysqlclient) expects
        # SSL config passed here via connect_args, not as a URI query param.
        # sqlite3.connect() has no 'ssl' kwarg, so this must stay gated to
        # the mysql dialect or local dev (LOCAL_SQLITE_URI) breaks.
        SQLALCHEMY_ENGINE_OPTIONS["connect_args"] = {"ssl": {"ssl": {}}}

    # --- Sessions / auth ---
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") != "development"
    SESSION_COOKIE_HTTPONLY = True

    # --- DigitalOcean Spaces (avatar / photo storage) ---
    SPACES_KEY = os.environ.get("SPACES_KEY", "")
    SPACES_SECRET = os.environ.get("SPACES_SECRET", "")
    SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "")
    SPACES_REGION = os.environ.get("SPACES_REGION", "nyc3")
    # Optional: a CDN-fronted domain for the Space (e.g. "my-bucket.nyc3.cdn.digitaloceanspaces.com").
    # If unset, falls back to the plain Spaces origin URL.
    SPACES_CDN_DOMAIN = os.environ.get("SPACES_CDN_DOMAIN", "")

    # --- Third-party services (populated later) ---
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    # Stripe Price IDs for self-serve subscription checkout -- one per
    # paid tier. These are NOT set by default; create the matching
    # Product/Price in the Stripe Dashboard (mode=recurring) for
    # "starter" ($15/mo flat) and "pro" ($12/seat/mo, per-unit pricing
    # with quantity = seat count), then set these env vars to the
    # resulting price IDs (price_...). "free" and "team" intentionally
    # have no entry: free has nothing to check out for, and team is
    # custom/invoiced -- see Org.billing_type -- not self-serve.
    STRIPE_PRICE_IDS = {
        "starter": os.environ.get("STRIPE_PRICE_ID_STARTER", ""),
        "pro": os.environ.get("STRIPE_PRICE_ID_PRO", ""),
    }

    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
    # Both the default From address and the org-level sending domain
    # below live on the SAME authenticated domain (mail.andgifts.app) on
    # purpose -- SendGrid Domain Authentication is per-domain, and having
    # notifications@ on the bare apex while org senders use a subdomain
    # meant only one of the two was ever actually authenticated. One
    # domain, one DNS setup, both paths covered.
    SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "notifications@mail.andgifts.app")
    # Domain-authenticated sending domain (SendGrid Domain Authentication +
    # matching DNS records) used for org-level flow-action email senders --
    # see Org.sender_from.
    SENDGRID_SENDING_DOMAIN = os.environ.get("SENDGRID_SENDING_DOMAIN", "mail.andgifts.app")
    # Inbox that receives Support form submissions (see routes/support.py).
    SUPPORT_INBOX_EMAIL = os.environ.get("SUPPORT_INBOX_EMAIL", "support@andgifts.app")
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    # --- One-off gift orders ---
    # Flat-rate shipping charged on top of the gift price when the agent
    # picks "ship it" instead of "pickup" at checkout. Pickup is free.
    FLAT_RATE_SHIPPING_CENTS = int(os.environ.get("FLAT_RATE_SHIPPING_CENTS", "595"))
    # Single hardcoded pickup location for now -- revisit if a second
    # location (or a per-org pickup address) is ever needed.
    PICKUP_LOCATION_ADDRESS = os.environ.get(
        "PICKUP_LOCATION_ADDRESS", "1096 E 50 S, American Fork, UT"
    )

    # --- Tier limits (single source of truth for enforcement) ---
    # "email"/"sms" booleans intentionally removed (2026-08) -- channel
    # access is universal across every tier now, matching the actual
    # product philosophy: the subscription's job is retention/contact
    # capacity, gifts (and their DHRB/WDF margin) carry the revenue, and
    # gating relationship-building tools by tier worked against that.
    # Nothing in the codebase read those two keys, so removing them is
    # safe -- confirmed via grep before deleting.
    #
    # email_monthly_cap / contact_cooldown_days are new: not about
    # revenue, about cost + deliverability. Email is nearly free
    # marginally to us so it's the one channel with no natural ceiling
    # an agent would hit on their own -- these stop "100 contacts,
    # emailed a million times" without reintroducing per-tier feature
    # gating. Enforced in Org.can_send_email_now(). sms_monthly_cap is
    # here for when text sending actually gets wired up to a real send
    # (still manual today -- see dashboard.approve_action) so the limit
    # exists in config ahead of the code that will enforce it.
    # flow_triggers (running existing flows/campaigns to generate
    # SuggestedActions) is universal across every tier including free --
    # ai_recommendations (the engine that suggests NEW flows an agent
    # doesn't have yet, via generate_flow_recommendations_for_user) is
    # the paid-only feature. See app/routes/dashboard.py and
    # jobs/generate_daily_suggestions.py, which gate on these
    # independently rather than as a single combined flag.
    TIER_LIMITS = {
        "free": {"contacts": 25, "seats": 1, "flow_triggers": True, "ai_recommendations": False, "email_monthly_cap": 100, "sms_monthly_cap": 25, "contact_cooldown_days": 5},
        "starter": {"contacts": 100, "seats": 1, "flow_triggers": True, "ai_recommendations": True, "email_monthly_cap": 300, "sms_monthly_cap": 50, "contact_cooldown_days": 5},
        "pro": {"contacts": 1000, "seats": 5, "flow_triggers": True, "ai_recommendations": True, "email_monthly_cap": 1000, "sms_monthly_cap": 200, "contact_cooldown_days": 5},
        "team": {"contacts": None, "seats": None, "flow_triggers": True, "ai_recommendations": True, "email_monthly_cap": None, "sms_monthly_cap": None, "contact_cooldown_days": 5},
    }

    # Low-to-high ordering of tiers -- lets webhook handlers tell an
    # upgrade from a downgrade (see routes/orders.py) without hardcoding
    # tier comparisons in more than one place.
    TIER_ORDER = ["free", "starter", "pro", "team"]

    # Where signup/upgrade/downgrade notification emails go (see
    # services/org_events.py). Separate from SUPPORT_INBOX_EMAIL since
    # these are business-activity pings, not customer support requests.
    PLATFORM_ADMIN_EMAIL = os.environ.get("PLATFORM_ADMIN_EMAIL", "admin@andgifts.app")

    # --- Public pricing page display (marketing copy only -- NOT wired to
    # Stripe or enforcement yet; that's the next step). price_cents is
    # display-only for now; TIER_LIMITS above remains the single source of
    # truth for what each tier actually gets. Keeping both in one place
    # (config.py) so the numbers can't silently drift apart when someone
    # tunes one and forgets the other.
    PRICING_DISPLAY = {
        "free": {
            "display_name": "Free", "price_cents": 0, "price_suffix": "/mo",
            "tagline": "Try it out", "cta_label": "Start free", "highlight": False,
        },
        "starter": {
            "display_name": "Solo", "price_cents": 1500, "price_suffix": "/mo",
            "tagline": "For solo agents", "cta_label": "Get Solo", "highlight": False,
        },
        "pro": {
            "display_name": "Pro", "price_cents": 1200, "price_suffix": "/seat/mo",
            "tagline": "For small teams (up to 5 seats)", "cta_label": "Get Pro", "highlight": True,
        },
        "team": {
            "display_name": "Team", "price_cents": None, "price_suffix": "",
            "tagline": "For brokerages", "cta_label": "Contact us", "highlight": False,
        },
    }


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
