import os
import secrets
from functools import wraps

from flask import jsonify, request
from flask_login import current_user


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


def api_session_or_token_required(f):
    """Allow Flask-Login session or DSIGN_API_TOKEN Bearer (monitoring scripts)."""

    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_user.is_authenticated or api_token_authorized():
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

    return wrapped
