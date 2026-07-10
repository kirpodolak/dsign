"""Wi-Fi SSID and WPA password validation (backlog H-WIFI)."""

from __future__ import annotations


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def validate_wifi_ssid(ssid: str) -> tuple[bool, str | None]:
    """
    Validate SSID for nmcli connect.

    Rules: 1–32 UTF-8 bytes, no ASCII control characters (incl. DEL).
    """
    cleaned = (ssid or "").strip()
    if not cleaned:
        return False, "SSID is required"
    if _has_control_chars(cleaned):
        return False, "SSID contains invalid control characters"
    if len(cleaned.encode("utf-8")) > 32:
        return False, "SSID must be at most 32 bytes"
    return True, None


def validate_wifi_password(password: str | None) -> tuple[bool, str | None]:
    """
    Validate WPA password when provided.

    Empty or omitted password is allowed (open network). Otherwise 8–63 chars.
    """
    if password is None:
        return True, None
    if password == "":
        return True, None
    if _has_control_chars(password):
        return False, "WPA password contains invalid control characters"
    if len(password) < 8:
        return False, "WPA password must be at least 8 characters"
    if len(password) > 63:
        return False, "WPA password must be at most 63 characters"
    return True, None
