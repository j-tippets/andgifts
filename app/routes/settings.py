from flask import Blueprint, render_template
from flask_login import login_required

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/")
@login_required
def index():
    """Settings hub. Deliberately thin -- it's a landing page that links
    out to the actual settings surfaces (custom fields today; team and
    profile are already reachable from here too, even though they also
    have their own direct links in the account menu). Add new settings
    sections here as they're built rather than growing this file itself."""
    return render_template("settings/index.html")
