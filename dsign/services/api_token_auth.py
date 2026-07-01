import os
import secrets
from functools import wraps

from flask import g, jsonify, request
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


def _api_bearer_should_skip_csrf() -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return False
    if not (request.path or "").startswith("/api/"):
        return False
    return api_token_authorized()


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


def _register_csrf_json_errors(app) -> None:
    if getattr(app, "_dsign_csrf_json_handler", False):
        return
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(exc):
        if (request.path or "").startswith("/api/"):
            return jsonify({
                "success": False,
                "error": "Bad Request",
                "message": getattr(exc, "description", None) or str(exc) or "The CSRF token is missing.",
            }), 400
        return jsonify({"success": False, "error": str(exc)}), 400

    app._dsign_csrf_json_handler = True


def configure_api_csrf_auth(app) -> None:
    """
    Wire Bearer-token API auth with Flask-WTF CSRF.

    Production create_app() may register CSRFProtect twice (extensions + create_app),
    leaving two before_request handlers. Patch CSRFProtect.protect at class level so
    every handler skips CSRF for valid Bearer on /api/*. Session UI calls still
    validate X-CSRFToken inside api_session_or_token_required.
    """
    csrf_ext = app.extensions.get("csrf")
    if csrf_ext is None:
        return

    _register_csrf_json_errors(app)
    _patch_csrf_protect_for_bearer()

    for view in app.view_functions.values():
        if getattr(view, "csrf_exempt", False):
            csrf_ext.exempt(view)


def _patch_csrf_protect_for_bearer() -> None:
    from flask_wtf.csrf import CSRFProtect

    if getattr(CSRFProtect, "_dsign_bearer_patched", False):
        return

    original_protect = CSRFProtect.protect

    def protect(self, apply_exemptions=False):
        if _api_bearer_should_skip_csrf():
            g.csrf_valid = True
            return
        return original_protect(self, apply_exemptions)

    CSRFProtect.protect = protect
    CSRFProtect._dsign_bearer_patched = True


def register_api_csrf_exemptions(app) -> None:
    """Backward-compatible alias."""
    configure_api_csrf_auth(app)
