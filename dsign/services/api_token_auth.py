import os
import secrets
from functools import wraps

from flask import jsonify, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf


def _configured_api_token() -> str:
    return (os.getenv("DSIGN_API_TOKEN") or "").strip()


def api_token_authorized() -> bool:
    """True when Authorization: Bearer matches DSIGN_API_TOKEN (if configured)."""
    expected = _configured_api_token()
    if not expected:
        return False
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return False
    provided = auth[7:].strip()
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


def _validate_session_csrf() -> tuple[bool, str | None]:
    """Session-authenticated API POST/PUT/PATCH/DELETE must still send CSRF."""
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return True, None
    token = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
    if not token:
        return False, "The CSRF token is missing."
    try:
        validate_csrf(token)
    except Exception as exc:
        return False, str(exc) or "Invalid CSRF token"
    return True, None


def api_session_or_token_required(f):
    """Allow Flask-Login session or DSIGN_API_TOKEN Bearer (monitoring scripts)."""

    @wraps(f)
    def wrapped(*args, **kwargs):
        if api_token_authorized():
            return f(*args, **kwargs)
        if current_user.is_authenticated:
            ok, message = _validate_session_csrf()
            if not ok:
                return jsonify({
                    "success": False,
                    "error": "Bad Request",
                    "message": message or "The CSRF token is missing.",
                }), 400
            return f(*args, **kwargs)
        return jsonify({
            "success": False,
            "error": "Authentication required",
            "authenticated": False,
            "hint": (
                "Log in via /api/auth/login (session cookie) or set DSIGN_API_TOKEN "
                "and send Authorization: Bearer <token>."
            ),
        }), 401

    wrapped.csrf_exempt = True
    return wrapped


def register_api_csrf_exemptions(app) -> None:
    """Mark Bearer-capable API views CSRF-exempt; session calls validate CSRF manually."""
    csrf = app.extensions.get("csrf")
    if csrf is None:
        return
    for view in app.view_functions.values():
        if getattr(view, "csrf_exempt", False):
            csrf.exempt(view)
