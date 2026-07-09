"""API rate limiting tests (backlog H-RL)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask_wtf.csrf import generate_csrf

from dsign.services.api_rate_limit import (
    GLOBAL_MAX_REQUESTS,
    RATE_LIMIT_PLAYBACK_PLAY,
    check_rate_limit,
    reset_api_rate_limits,
)


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    reset_api_rate_limits()
    yield
    reset_api_rate_limits()


def test_check_rate_limit_allows_until_max():
    key = "127.0.0.1:test"
    max_req, window = 3, 60.0

    assert check_rate_limit(key, max_req, window) is True
    assert check_rate_limit(key, max_req, window) is True
    assert check_rate_limit(key, max_req, window) is True
    assert check_rate_limit(key, max_req, window) is False


def test_check_rate_limit_resets_after_window(monkeypatch):
    key = "127.0.0.1:window"
    max_req, window = 2, 10.0
    t0 = datetime(2026, 7, 9, 12, 0, 0)

    class _FakeDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return t0

    monkeypatch.setattr("dsign.services.api_rate_limit.datetime", _FakeDatetime)
    assert check_rate_limit(key, max_req, window) is True
    assert check_rate_limit(key, max_req, window) is True
    assert check_rate_limit(key, max_req, window) is False

    class _LaterDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return t0 + timedelta(seconds=window + 1)

    monkeypatch.setattr("dsign.services.api_rate_limit.datetime", _LaterDatetime)
    assert check_rate_limit(key, max_req, window) is True


def test_playback_play_returns_429_after_limit(api_client):
    client, _app, _user, playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}
    max_play, _window = RATE_LIMIT_PLAYBACK_PLAY
    body = {"playlist_id": playlist.id}

    for _ in range(max_play):
        rv = client.post("/api/playback/play", json=body, headers=headers)
        assert rv.status_code == 200

    rv = client.post("/api/playback/play", json=body, headers=headers)
    assert rv.status_code == 429
    assert rv.get_json().get("success") is False
    assert "Too many requests" in (rv.get_json().get("error") or "")


def test_playback_stop_returns_429_after_limit(api_client):
    client, _app, _user, _playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    for _ in range(10):
        rv = client.post("/api/playback/stop", headers=headers)
        assert rv.status_code == 200

    rv = client.post("/api/playback/stop", headers=headers)
    assert rv.status_code == 429


def test_global_api_rate_limit_blocks_excess_requests(api_client):
    client, _app, _user, _playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    for _ in range(GLOBAL_MAX_REQUESTS):
        rv = client.get("/api/health", headers=headers)
        assert rv.status_code == 200

    rv = client.get("/api/health", headers=headers)
    assert rv.status_code == 429


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


def test_screenshot_capture_returns_429_after_limit(api_client, monkeypatch):
    client, _app, user, _playlist = api_client
    _login_session(client, user)
    headers = {
        **_csrf_headers(client),
        "X-DSIGN-Preview-Intent": "manual",
    }

    monkeypatch.setattr(
        "dsign.routes.api.api_routes.settings_service",
        MagicMock(get_current_settings=lambda: {"display": {"preview_auto_interval_sec": 0}}),
        raising=False,
    )

    with patch("dsign.routes.api.api_routes.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("dsign.routes.api.api_routes.os.path.exists", return_value=False):
            for _ in range(6):
                rv = client.post("/api/media/mpv_screenshot/capture", headers=headers)
                assert rv.status_code in (200, 500)

            rv = client.post("/api/media/mpv_screenshot/capture", headers=headers)
            assert rv.status_code == 429
