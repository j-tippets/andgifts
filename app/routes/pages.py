"""
Public top-of-funnel pages: Privacy Policy, Terms & Conditions, Cookie
Policy, Refund Policy, About/Contact. All placeholder content -- real
legal copy should replace these before relying on them for App Store /
Play Store review or actual compliance. No login required; these need
to be reachable by someone who hasn't signed up yet (and by app store
reviewers, who won't have an account).
"""
from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)

UPDATED_DATE = "August 2026"


@pages_bp.route("/privacy")
def privacy():
    return render_template("pages/privacy.html", updated_date=UPDATED_DATE)


@pages_bp.route("/terms")
def terms():
    return render_template("pages/terms.html", updated_date=UPDATED_DATE)


@pages_bp.route("/cookies")
def cookies():
    return render_template("pages/cookies.html", updated_date=UPDATED_DATE)


@pages_bp.route("/refund-policy")
def refund_policy():
    return render_template("pages/refund_policy.html", updated_date=UPDATED_DATE)


@pages_bp.route("/about")
def about():
    return render_template("pages/about.html", updated_date=UPDATED_DATE)
