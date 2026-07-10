"""TTL cleanup for per-media playback backoff (backlog H-MEM)."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, MutableMapping

DEFAULT_MEDIA_BACKOFF_TTL_SEC = 3600.0  # 1 hour


def media_backoff_ttl_sec() -> float:
    raw = (os.getenv("DSIGN_MEDIA_BACKOFF_TTL_SEC") or "3600").strip()
    try:
        return max(60.0, min(86400.0, float(raw)))
    except ValueError:
        return DEFAULT_MEDIA_BACKOFF_TTL_SEC


def entry_last_touch_monotonic(entry: Dict[str, Any]) -> float:
    try:
        return float(entry.get("last_touch_monotonic") or entry.get("next_try_monotonic") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def prune_stale_media_backoff(
    backoff: MutableMapping[str, Dict[str, Any]],
    *,
    now: float | None = None,
    ttl_sec: float | None = None,
) -> int:
    """
    Drop entries not touched within ``ttl_sec``.

    Entries without a usable timestamp are removed as corrupt/stale.
    """
    if not backoff:
        return 0

    now_f = time.monotonic() if now is None else float(now)
    ttl = media_backoff_ttl_sec() if ttl_sec is None else float(ttl_sec)
    stale_keys = [
        key
        for key, entry in backoff.items()
        if entry_last_touch_monotonic(entry) <= 0.0 or (now_f - entry_last_touch_monotonic(entry)) > ttl
    ]
    for key in stale_keys:
        backoff.pop(key, None)
    return len(stale_keys)
