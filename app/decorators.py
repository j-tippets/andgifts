from functools import wraps
from flask import abort
from flask_login import current_user, login_required


def admin_required(view_func):
    """Use alongside @login_required (or on its own) to restrict a route
    to org admins only -- e.g. team management, org settings."""
    @wraps(view_func)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapped
