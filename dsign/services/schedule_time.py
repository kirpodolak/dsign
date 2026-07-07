"""Timezone-aware schedule clock (D2)."""

from __future__ import annotations

import zoneinfo
from datetime import datetime
from typing import Any, Optional

DEFAULT_TZ = "Europe/Moscow"


def local_now(settings_service: Optional[Any] = None) -> datetime:
    """Current local time in settings timezone (aware datetime)."""
    tz_name = DEFAULT_TZ
    if settings_service is not None:
        try:
            tz_name = settings_service.load_settings().get("timezone", DEFAULT_TZ)
        except Exception:
            pass
    try:
        return datetime.now(zoneinfo.ZoneInfo(str(tz_name)))
    except Exception:
        return datetime.now()
