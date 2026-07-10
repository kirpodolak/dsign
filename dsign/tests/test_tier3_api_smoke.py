"""Tier 3 API smoke: Settings + System endpoints (pytest optional backlog)."""

from __future__ import annotations

from flask_wtf.csrf import generate_csrf


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


def test_settings_schema_requires_session(api_client):
    client, _app, _user, _playlist = api_client

    rv = client.get("/api/settings/schema")

    assert rv.status_code == 401


def test_settings_schema_returns_mpv_schema(api_client):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    rv = client.get("/api/settings/schema")

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert isinstance(body.get("schema"), dict)


def test_settings_current_returns_payload(api_client):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    rv = client.get("/api/settings/current")

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert "settings" in body


def test_settings_timezones_list(api_client):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    rv = client.get("/api/settings/timezones")

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert isinstance(body.get("timezones"), list)


def test_system_status_with_mocked_disk(api_client, monkeypatch):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    class _Usage:
        total = 1_000_000
        used = 400_000
        free = 600_000

    monkeypatch.setattr("dsign.routes.api.api_routes.shutil.disk_usage", lambda _p: _Usage())
    monkeypatch.setattr("dsign.routes.api.api_routes.Path.exists", lambda self: False)

    def _fake_run(cmd, **kwargs):
        from unittest.mock import MagicMock

        return MagicMock(returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr("dsign.routes.api.api_routes.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "dsign.routes.api.api_routes.subprocess.check_output",
        lambda *a, **k: "",
    )

    rv = client.get("/api/system/status")

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert "storage" in (body.get("status") or {})


def test_system_network_status_with_mocked_nmcli(api_client, monkeypatch):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    monkeypatch.setattr(
        "dsign.routes.api.api_routes.shutil.which",
        lambda name: "/usr/bin/nmcli" if name == "nmcli" else None,
    )

    def _fake_run(args, **kwargs):
        from unittest.mock import MagicMock

        cmd = list(args) if args else kwargs.get("args", [])
        if cmd[:2] == ["ip", "-4"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "nmcli":
            return MagicMock(returncode=0, stdout="wlan0:wifi:connected:MyWifi\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("dsign.routes.api.api_routes.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "dsign.routes.api.api_routes.subprocess.check_output",
        lambda *a, **k: "",
    )

    rv = client.get("/api/system/network/status")

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert "network" in body


def test_system_services_status_with_mocked_systemctl(api_client, monkeypatch):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    def _fake_run(cmd, **kwargs):
        from unittest.mock import MagicMock

        return MagicMock(
            returncode=0,
            stdout="ActiveState=active\nSubState=running\n",
            stderr="",
        )

    monkeypatch.setattr("dsign.routes.api.api_routes.subprocess.run", _fake_run)

    rv = client.get("/api/system/services/status")

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert isinstance(body.get("services"), dict)


def test_settings_update_requires_csrf(api_client):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    rv = client.post("/api/settings/update", json={"volume": 50})

    assert rv.status_code == 400
    assert "CSRF" in (rv.get_json().get("message") or "")


def test_settings_update_with_csrf(api_client):
    client, _app, user, _playlist = api_client
    _login_session(client, user)

    rv = client.post(
        "/api/settings/update",
        json={"master_volume": 80},
        headers=_csrf_headers(client),
    )

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
