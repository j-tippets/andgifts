from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required
from app.extensions import db
from app.models import User, Org

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("auth.register"))

        org = Org(name=request.form.get("org_name", "My Business"), tier="free")
        db.session.add(org)
        db.session.flush()  # get org.id before creating user

        user = User(
            org_id=org.id,
            email=email,
            first_name=request.form.get("first_name", ""),
            last_name=request.form.get("last_name", ""),
            role="admin",
        )
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()

        login_user(user)
        return redirect(url_for("dashboard.index"))

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form["password"]):
            login_user(user)
            return redirect(url_for("dashboard.index"))
        flash("Invalid email or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
