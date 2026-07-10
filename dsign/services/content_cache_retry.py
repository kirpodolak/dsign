"""Download retry backoff for ContentCache (backlog H-CACHE)."""

from __future__ import annotations

import os

DEFAULT_CONTENT_CACHE_DOWNLOAD_ATTEMPTS = 3
DEFAULT_CONTENT_CACHE_RETRY_BASE_SEC = 2.0
DEFAULT_CONTENT_CACHE_RETRY_MAX_SEC = 120.0


def download_max_attempts() -> int:
    raw = (os.getenv("DSIGN_CONTENT_CACHE_DOWNLOAD_ATTEMPTS") or "").strip()
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return DEFAULT_CONTENT_CACHE_DOWNLOAD_ATTEMPTS


def download_retry_delay_sec(attempt: int) -> float:
    """
    Exponential backoff after failed attempt ``attempt`` (1-based).

    Delays: base, 2*base, 4*base, ... capped at max.
    """
    try:
        n = max(1, int(attempt))
    except (TypeError, ValueError):
        n = 1
    base_raw = (os.getenv("DSIGN_CONTENT_CACHE_RETRY_BASE_SEC") or "").strip()
    max_raw = (os.getenv("DSIGN_CONTENT_CACHE_RETRY_MAX_SEC") or "").strip()
    try:
        base = float(base_raw) if base_raw else DEFAULT_CONTENT_CACHE_RETRY_BASE_SEC
    except ValueError:
        base = DEFAULT_CONTENT_CACHE_RETRY_BASE_SEC
    try:
        cap = float(max_raw) if max_raw else DEFAULT_CONTENT_CACHE_RETRY_MAX_SEC
    except ValueError:
        cap = DEFAULT_CONTENT_CACHE_RETRY_MAX_SEC
    base = max(0.5, min(60.0, base))
    cap = max(base, min(600.0, cap))
    return min(cap, base * (2 ** min(n - 1, 6)))
