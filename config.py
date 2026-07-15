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
        # DO managed MySQL requires SSL. PyMySQL (not mysqlclient) expects
        # SSL config passed here via connect_args, not as a URI query param.
        "connect_args": {"ssl": {"ssl": {}}},
    }

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
    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
    SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "orders@andgifts.app")
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
    TIER_LIMITS = {
        "free": {"contacts": 25, "seats": 1, "email": False, "sms": False, "ai_dashboard": False},
        "starter": {"contacts": 100, "seats": 1, "email": True, "sms": False, "ai_dashboard": False},
        "pro": {"contacts": 1000, "seats": 5, "email": True, "sms": True, "ai_dashboard": True},
        "team": {"contacts": None, "seats": None, "email": True, "sms": True, "ai_dashboard": True},
    }


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
