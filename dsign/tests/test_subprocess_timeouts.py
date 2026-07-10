"""Subprocess timeout audit tests (backlog H-SUB)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask_wtf.csrf import generate_csrf

from dsign.services.subprocess_limits import (
    AMIXER_TIMEOUT_SEC,
    APLAY_LIST_TIMEOUT_SEC,
    DISPLAY_APPLY_TIMEOUT_SEC,
)


def _login_session(client, user) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _csrf_headers(client) -> dict:
    with client.session_transaction() as sess:
        with client.application.test_request_context():
            from flask import session

            session.update(dict(sess))
            token = generate_csrf()
            sess.update(dict(session))
    return {"X-CSRFToken": token}


def test_subprocess_limit_constants():
    assert AMIXER_TIMEOUT_SEC == 3.0
    assert APLAY_LIST_TIMEOUT_SEC == 5.0
    assert DISPLAY_APPLY_TIMEOUT_SEC == 90.0


def test_audio_set_amixer_passes_timeout(api_client, monkeypatch):
    """POST /api/system/audio must pass timeout= on every amixer subprocess call."""
    client, _app, user, _playlist = api_client
    _login_session(client, user)
    headers = _csrf_headers(client)

    monkeypatch.setenv("DSIGN_PREFER_MPV_VOLUME", "0")
    monkeypatch.setenv("DSIGN_USE_PIPEWIRE_AUDIO", "0")

    captured: list[tuple[str, list, dict]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(("run", list(cmd), dict(kwargs)))
        return MagicMock(returncode=0, stdout="", stderr="")

    def _fake_check_output(cmd, **kwargs):
        captured.append(("check_output", list(cmd), dict(kwargs)))
        if cmd[0] == "amixer" and "scontrols" in cmd:
            return "Simple mixer control 'PCM'\n"
        if cmd[0] == "amixer":
            return "  Playback 50% [on]\n"
        return ""

    orig_read_text = Path.read_text

    def _patched_read_text(self, *args, **kwargs):
        if str(self) == "/proc/asound/cards":
            return " 1 [vc4hdmi0]\n"
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr("dsign.routes.api.api_routes.subprocess.run", _fake_run)
    monkeypatch.setattr("dsign.routes.api.api_routes.subprocess.check_output", _fake_check_output)
    monkeypatch.setattr(Path, "read_text", _patched_read_text)

    rv = client.post("/api/system/audio", json={"volume_percent": 50}, headers=headers)

    assert rv.status_code == 200
    amixer_calls = [c for c in captured if c[1] and c[1][0] == "amixer"]
    assert amixer_calls, "expected amixer subprocess calls on vc4hdmi path"
    missing_timeout = [
        {"cmd": c[1], "timeout": c[2].get("timeout")}
        for c in amixer_calls
        if c[2].get("timeout") != AMIXER_TIMEOUT_SEC
    ]
    assert not missing_timeout, (
        "every amixer subprocess call must use AMIXER_TIMEOUT_SEC; "
        f"offenders: {missing_timeout}"
    )


def test_settings_aplay_list_uses_timeout(null_logger, tmp_path, monkeypatch):
    from dsign.services.settings_service import SettingsService

    settings_file = tmp_path / "settings.json"
    upload = tmp_path / "upload"
    upload.mkdir()
    svc = SettingsService(str(settings_file), str(upload), logger=null_logger)

    captured: list[dict] = []

    def _fake_run(cmd, **kwargs):
        captured.append(kwargs)
        return MagicMock(returncode=0, stdout="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    devices = svc._parse_aplay_hdmi_pch_devices()

    assert devices == []
    assert captured
    assert captured[0].get("timeout") == APLAY_LIST_TIMEOUT_SEC


def test_display_apply_handles_timeout(api_client, monkeypatch):
    client, _app, user, _playlist = api_client
    _login_session(client, user)
    headers = _csrf_headers(client)

    import subprocess

    def _timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else kwargs.get("args"),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr("dsign.routes.api.api_routes.subprocess.run", _timeout_run)

    rv = client.post(
        "/api/settings/display/apply",
        json={"preset": "auto", "reboot": False},
        headers=headers,
    )

    assert rv.status_code == 504
    assert "Timed out" in (rv.get_json().get("error") or "")
