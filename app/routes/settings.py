from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import admin_required
from app.models.org import Org, slugify_sender_local_part

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


@settings_bp.route("/sender", methods=["GET", "POST"])
@admin_required
def sender_identity():
    """Org-admin-only: the shared From address flow-action emails send
    from for every agent in this org (see Org.sender_from). No
    per-agent verification -- the sending domain is authenticated once
    (outside the app, in SendGrid + DNS), so any local-part on it just
    works. Auto-generated at org creation; editable here afterward."""
    org = current_user.org

    if request.method == "POST":
        raw = request.form.get("sender_local_part", "").strip()
        candidate = slugify_sender_local_part(raw)
        if not candidate or (candidate == "agency" and raw):
            flash(
                "That name didn't leave anything usable after removing spaces/punctuation -- try letters and numbers.",
                "error",
            )
            return redirect(url_for("settings.sender_identity"))

        existing = Org.query.filter(Org.sender_local_part == candidate, Org.id != org.id).first()
        if existing:
            flash(f"\"{candidate}\" is already in use by another org on &Gifts -- try something more specific.", "error")
            return redirect(url_for("settings.sender_identity"))

        org.sender_local_part = candidate
        db.session.commit()
        flash("Sender address updated.", "success")
        return redirect(url_for("settings.sender_identity"))

    domain = current_app.config.get("SENDGRID_SENDING_DOMAIN")
    return render_template("settings/sender.html", org=org, domain=domain)
