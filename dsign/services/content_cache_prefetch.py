"""ContentCache prefetch pool helpers (backlog H-PREF)."""

from __future__ import annotations

import os


def prefetch_workers() -> int:
    raw = (os.getenv("DSIGN_CONTENT_CACHE_PREFETCH_WORKERS") or "1").strip()
    try:
        return max(1, min(4, int(raw)))
    except ValueError:
        return 1
