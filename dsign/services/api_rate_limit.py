"""In-memory per-IP API rate limiting (backlog H-RL).

Mirrors the login rate limiter in auth_routes.py — no extra dependencies.
"""

from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from threading import Lock
from typing import Any, Callable, Optional, TypeVar

from flask import jsonify, request

F = TypeVar("F", bound=Callable[..., Any])

_lock = Lock()
_buckets: dict[str, dict[str, Any]] = {}

GLOBAL_MAX_REQUESTS = int(os.environ.get("DSIGN_API_GLOBAL_RATE_MAX", "100"))
GLOBAL_WINDOW_SEC = float(os.environ.get("DSIGN_API_GLOBAL_RATE_WINDOW_SEC", "60"))

RATE_LIMIT_PLAYBACK_PLAY = (5, 60.0)
RATE_LIMIT_PLAYBACK_STOP = (10, 60.0)
RATE_LIMIT_SCREENSHOT_CAPTURE = (6, 60.0)
RATE_LIMIT_SERVICE_RESTART = (3, 60.0)
RATE_LIMIT_SYSTEM_REBOOT = (1, 3600.0)


def client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "unknown")


def _is_loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost")


def check_rate_limit(key: str, max_requests: int, window_seconds: float) -> bool:
    """Return True when the request is within the limit."""
    with _lock:
        now = datetime.utcnow()
        bucket = _buckets.get(key)
        if bucket is None:
            _buckets[key] = {"count": 1, "start": now}
            return True

        elapsed = (now - bucket["start"]).total_seconds()
        if elapsed > window_seconds:
            _buckets[key] = {"count": 1, "start": now}
            return True

        if bucket["count"] >= max_requests:
            return False

        bucket["count"] += 1
        return True


def reset_api_rate_limits() -> None:
    """Clear all counters (pytest and admin test hooks)."""
    with _lock:
        _buckets.clear()


def rate_limit_response() -> tuple[Any, int]:
    return (
        jsonify({
            "success": False,
            "error": "Too many requests. Please try again later.",
        }),
        429,
    )


def enforce_global_api_rate_limit() -> Optional[tuple[Any, int]]:
    ip = client_ip()
    if _is_loopback(ip) and os.environ.get("DSIGN_API_RATE_LIMIT_LOOPBACK", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None
    key = f"{ip}:__global__"
    if not check_rate_limit(key, GLOBAL_MAX_REQUESTS, GLOBAL_WINDOW_SEC):
        return rate_limit_response()
    return None


def api_rate_limit(max_requests: int, window_seconds: float, *, bucket: str) -> Callable[[F], F]:
    """Per-IP limiter for a named API bucket (in addition to the global limit)."""

    def decorator(f: F) -> F:
        @wraps(f)
        def wrapped(*args: Any, **kwargs: Any):
            ip = client_ip()
            key = f"{ip}:{bucket}"
            if not check_rate_limit(key, max_requests, window_seconds):
                return rate_limit_response()
            return f(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator
