import os
from flask import Flask
from config import config_by_name
from app.extensions import db, migrate, login_manager, limiter


def _compute_static_asset_version(app):
    """Fallback for local dev only (no GIT_COMMIT_HASH env var there) --
    see create_app's comment on STATIC_ASSET_VERSION for why this is
    NOT what production uses. Hash of every filename+mtime under
    static/css, static/js, and static/icons.

    Used two ways: (1) as sw.js's CACHE_VERSION, so the service
    worker's cache bucket changes whenever any of those files change,
    and (2) appended as a ?v= query string on every CSS/JS
    <link>/<script> tag via the versioned_static() Jinja global below.

    The ?v= part is what actually matters for correctness -- it
    changes the REQUEST URL itself on a deploy, so a stale cache (the
    browser's HTTP cache, an old still-active service worker, or a CDN
    in front of the app) simply can't have an entry for it and has to
    fetch fresh, regardless of whether the service worker's own
    install/activate handoff has completed yet."""
    import hashlib

    hasher = hashlib.sha1()
    for subdir in ("css", "js", "icons"):
        dir_path = os.path.join(app.static_folder, subdir)
        if not os.path.isdir(dir_path):
            continue
        for name in sorted(os.listdir(dir_path)):
            file_path = os.path.join(dir_path, name)
            if os.path.isfile(file_path):
                hasher.update(name.encode())
                hasher.update(str(os.path.getmtime(file_path)).encode())
    return hasher.hexdigest()[:12]


def create_app(config_name=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "production")
    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name if config_name in config_by_name else "production"])

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    limiter.init_app(app)

    # STATIC_ASSET_VERSION drives cache-busting for every CSS/JS asset
    # (see versioned_static below) and the service worker's cache
    # bucket name. Prefer GIT_COMMIT_HASH (set in .do/app.yaml from DO
    # App Platform's bindable ${web.COMMIT_HASH}) over computing it
    # locally from file mtimes -- mtime hashing is computed
    # independently BY EACH RUNNING PROCESS from its own container's
    # filesystem, so during any rolling deploy (DO briefly runs the
    # old and new containers side by side, even at instance_count: 1)
    # the old and new containers compute two DIFFERENT hashes for what
    # might genuinely be identical file content, and whichever one a
    # given request lands on determines which version it sees -- with
    # no single deploy ever fully "finishing" that inconsistency from
    # the outside, since every subsequent deploy repeats the same
    # transient split. GIT_COMMIT_HASH sidesteps this entirely: every
    # instance of the SAME deployed commit agrees on the SAME version
    # string by construction, deterministically, regardless of local
    # mtimes. Falls back to the mtime hash only when that env var
    # isn't set (local dev, where there's just one process anyway and
    # this distinction doesn't matter).
    app.config["STATIC_ASSET_VERSION"] = os.environ.get("GIT_COMMIT_HASH") or _compute_static_asset_version(app)

    @app.template_global()
    def versioned_static(filename):
        from flask import url_for
        return f"{url_for('static', filename=filename)}?v={app.config['STATIC_ASSET_VERSION']}"

    @app.template_global()
    def current_static_version():
        # Exposed to templates so the page can stamp the version it was
        # rendered with (see base.html's <html data-app-version> and
        # static/js/version-check.js) -- same value versioned_static()
        # uses for cache-busting, just surfaced for client-side
        # comparison instead of a URL query string.
        return app.config["STATIC_ASSET_VERSION"]

    @app.template_global()
    def pop_pending_events():
        # See app/services/analytics.py -- base.html calls this on
        # every render to flush server-queued GTM/GA4 events.
        from app.services.analytics import pop_pending_events as _pop
        return _pop()

    @app.template_global()
    def condition_value_less_operators():
        # Single source of truth stays campaign_rules.VALUE_LESS_OPERATORS --
        # exposed as a template global (rather than threaded through every
        # condition_builder() call site) so the macro and its JS can both
        # know which operators (is_empty, is_checked, etc.) need no value
        # box without every caller having to remember to pass it.
        from app.services.campaign_rules import VALUE_LESS_OPERATORS
        return sorted(VALUE_LESS_OPERATORS)

    @app.template_global()
    def paid_action_types():
        # Single source of truth stays models.actions.PAID_ACTION_TYPES --
        # exposed so templates (contacts/view.html's Undo approval button)
        # can match the same gift/handwritten_note set dashboard.py's
        # unapprove_action enforces server-side, instead of a second
        # hardcoded literal that could silently drift from it.
        from app.models.actions import PAID_ACTION_TYPES
        return PAID_ACTION_TYPES

    # Import models so Flask-Migrate can see them for autogenerate
    from app import models  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(user_id)

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        # Matches the app's existing flash+redirect convention rather
        # than introducing a standalone error-page template (there
        # isn't one for any other status code either). request.referrer
        # falls back to login since a rate-limited request is always on
        # an auth-adjacent, unauthenticated route.
        from flask import request, redirect, url_for, flash
        flash("Too many attempts. Please wait a bit and try again.", "error")
        return redirect(request.referrer or url_for("auth.login"))

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.contacts import contacts_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.team import team_bp
    from app.routes.profile import profile_bp
    from app.routes.catalog import catalog_bp
    from app.routes.app_admin import app_admin_bp
    from app.routes.campaigns import campaigns_bp
    from app.routes.orders import orders_bp
    from app.routes.settings import settings_bp
    from app.routes.support import support_bp
    from app.routes.pages import pages_bp
    from app.routes.billing import billing_bp
    from app.routes.onboarding import onboarding_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(app_admin_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(billing_bp)

    from app.cli import register_cli
    register_cli(app)

    @app.route("/")
    def home():
        from flask import render_template, redirect, url_for
        from flask_login import current_user

        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return render_template("home.html")

    @app.route("/sw.js")
    def service_worker():
        # Served from root (not /static/sw.js) so the browser's default
        # service worker scope is "/" instead of "/static/" -- without
        # this, the SW would never control /dashboard (the manifest's
        # start_url) and the app would fail PWA installability checks.
        from flask import render_template

        response = app.response_class(
            render_template("sw.js.jinja", cache_version=app.config["STATIC_ASSET_VERSION"]),
            mimetype="application/javascript",
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/api/app-version")
    def app_version():
        # Polled client-side by static/js/version-check.js so a long-lived
        # open tab/session can notice a deploy happened and prompt a
        # refresh, instead of silently running a mix of old and new
        # assets (see base.html's data-app-version and the comment on
        # STATIC_ASSET_VERSION above for the failure mode this covers).
        # Deliberately not behind login_required: a session hiccup around
        # a deploy is exactly when this should still work.
        from flask import jsonify

        response = jsonify({"version": app.config["STATIC_ASSET_VERSION"]})
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
