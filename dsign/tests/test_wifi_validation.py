"""Wi-Fi SSID/password validation tests (backlog H-WIFI)."""

from __future__ import annotations

import pytest

from dsign.services.wifi_validation import validate_wifi_password, validate_wifi_ssid


@pytest.mark.parametrize(
    "ssid",
    [
        "MyNetwork",
        "a",
        "x" * 32,
        "Café-Guest",
    ],
)
def test_validate_wifi_ssid_accepts_valid(ssid: str):
    ok, err = validate_wifi_ssid(ssid)
    assert ok is True
    assert err is None


@pytest.mark.parametrize(
    ("ssid", "message"),
    [
        ("", "SSID is required"),
        ("   ", "SSID is required"),
        ("net\x00work", "control characters"),
        ("net\x7fwork", "control characters"),
        ("x" * 33, "32 bytes"),
        ("ü" * 17, "32 bytes"),
    ],
)
def test_validate_wifi_ssid_rejects_invalid(ssid: str, message: str):
    ok, err = validate_wifi_ssid(ssid)
    assert ok is False
    assert message.lower() in (err or "").lower()


@pytest.mark.parametrize(
    "password",
    [None, "", "12345678", "x" * 63],
)
def test_validate_wifi_password_accepts_valid(password):
    ok, err = validate_wifi_password(password)
    assert ok is True
    assert err is None


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short", "at least 8"),
        ("x" * 64, "at most 63"),
        ("goodpass\x00", "control characters"),
    ],
)
def test_validate_wifi_password_rejects_invalid(password: str, message: str):
    ok, err = validate_wifi_password(password)
    assert ok is False
    assert message.lower() in (err or "").lower()
