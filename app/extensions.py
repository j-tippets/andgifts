from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
# In-memory storage -- correct as long as the app runs as a single
# instance (confirmed current setup). If DigitalOcean ever scales this
# to multiple instances, each instance would track its own counts
# independently, letting someone get multiples of the intended limit
# by landing on different instances -- switch the storage_uri to a
# shared Redis/Valkey instance before scaling out.
limiter = Limiter(key_func=get_remote_address)
# Global CSRF protection for every state-changing request (see
# create_app's csrf.init_app call). Individual routes that genuinely
# can't carry a CSRF token -- Stripe's webhook, which POSTs from
# Stripe's own servers with no session/cookie at all -- are opted out
# explicitly with @csrf.exempt rather than this being disabled
# app-wide.
csrf = CSRFProtect()
