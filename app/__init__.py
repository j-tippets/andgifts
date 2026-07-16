import os
from flask import Flask
from config import config_by_name
from app.extensions import db, migrate, login_manager


def create_app(config_name=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "production")
    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name if config_name in config_by_name else "production"])

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # Import models so Flask-Migrate can see them for autogenerate
    from app import models  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(user_id)

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(app_admin_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(orders_bp)

    @app.route("/sw.js")
    def service_worker():
        # Served from root (not /static/sw.js) so the browser's default
        # service worker scope is "/" instead of "/static/" -- without
        # this, the SW would never control /dashboard (the manifest's
        # start_url) and the app would fail PWA installability checks.
        import hashlib
        import os
        from flask import render_template

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
        cache_version = hasher.hexdigest()[:12]

        response = app.response_class(
            render_template("sw.js.jinja", cache_version=cache_version),
            mimetype="application/javascript",
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    return app
