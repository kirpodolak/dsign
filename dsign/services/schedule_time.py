"""Timezone-aware schedule clock (D2)."""

from __future__ import annotations

import zoneinfo
from datetime import datetime
from typing import Any, Optional

DEFAULT_TZ = "Europe/Moscow"

# Curated list for Settings UI (D2.4); schedule engine accepts any valid IANA name.
SCHEDULE_TIMEZONE_OPTIONS = [
    "Europe/Kaliningrad",
    "Europe/Moscow",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Yakutsk",
    "Asia/Vladivostok",
    "Asia/Magadan",
    "Asia/Kamchatka",
    "Europe/Kyiv",
    "Europe/Minsk",
    "UTC",
]


def validate_timezone(tz_name: str) -> str:
    name = str(tz_name or "").strip()
    if not name:
        raise ValueError("timezone is required")
    try:
        zoneinfo.ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"invalid timezone: {name}") from exc
    return name


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
