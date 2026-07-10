"""API smoke tests for auth, Bearer, schedule, CSRF (backlog T-API)."""

from __future__ import annotations

from flask_wtf.csrf import generate_csrf


def _weekly_rule_body(playlist_id: int) -> dict:
    return {
        "playlist_id": playlist_id,
        "days_of_week": 127,
        "start_time": "09:00",
        "end_time": "18:00",
        "repeat_type": "weekly",
        "priority": 5,
    }


def _login_session(client, user) -> None:
    """Establish Flask-Login session without exercising login form CSRF (not T-API scope)."""
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


def test_unauthenticated_schedule_rules_returns_401(api_client):
    client, _app, _user, _playlist = api_client

    rv = client.get("/api/schedule/rules")

    assert rv.status_code == 401
    body = rv.get_json()
    assert body.get("authenticated") is False


def test_bearer_can_list_schedule_rules(api_client):
    client, _app, _user, playlist = api_client

    headers = {"Authorization": "Bearer test-bearer-token"}
    create = client.post(
        "/api/schedule/rules",
        json=_weekly_rule_body(playlist.id),
        headers=headers,
    )
    assert create.status_code == 201

    rv = client.get("/api/schedule/rules", headers=headers)

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert len(body.get("rules") or []) >= 1


def test_bearer_can_expand_schedule_month(api_client):
    client, _app, _user, playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    client.post(
        "/api/schedule/rules",
        json=_weekly_rule_body(playlist.id),
        headers=headers,
    )

    rv = client.get("/api/schedule/month?date=2026-07-01", headers=headers)

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert "instances" in body


def test_session_post_without_csrf_returns_400(api_client):
    client, _app, user, playlist = api_client
    _login_session(client, user)

    rv = client.post("/api/schedule/rules", json=_weekly_rule_body(playlist.id))

    assert rv.status_code == 400
    body = rv.get_json()
    assert "CSRF" in (body.get("message") or "")


def test_session_post_with_csrf_succeeds(api_client):
    client, _app, user, playlist = api_client
    _login_session(client, user)

    rv = client.post(
        "/api/schedule/rules",
        json=_weekly_rule_body(playlist.id),
        headers=_csrf_headers(client),
    )

    assert rv.status_code == 201
    assert rv.get_json().get("success") is True


def test_bearer_post_schedule_exception_without_csrf(api_client):
    client, _app, _user, playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    created = client.post(
        "/api/schedule/rules",
        json=_weekly_rule_body(playlist.id),
        headers=headers,
    )
    rule_id = created.get_json()["rule"]["id"]

    rv = client.post(
        "/api/schedule/exceptions",
        json={"rule_id": rule_id, "date": "2026-07-10"},
        headers=headers,
    )

    assert rv.status_code == 201
    assert rv.get_json().get("success") is True


def test_invalid_bearer_returns_401(api_client):
    client, _app, _user, _playlist = api_client

    rv = client.get(
        "/api/schedule/rules",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert rv.status_code == 401


def test_bearer_health_endpoint(api_client):
    client, _app, _user, _playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    rv = client.get("/api/health", headers=headers)

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True


def test_bearer_can_read_schedule_week(api_client):
    client, _app, _user, playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    client.post(
        "/api/schedule/rules",
        json=_weekly_rule_body(playlist.id),
        headers=headers,
    )

    rv = client.get("/api/schedule/week?date=2026-07-07", headers=headers)

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True
    assert "instances" in body


def test_bearer_can_read_schedule_now(api_client):
    client, _app, _user, _playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    rv = client.get("/api/schedule/now", headers=headers)

    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("success") is True


def test_bearer_return_to_schedule_without_csrf(api_client):
    client, _app, _user, _playlist = api_client
    headers = {"Authorization": "Bearer test-bearer-token"}

    rv = client.post("/api/playback/return-to-schedule", headers=headers)

    assert rv.status_code in (200, 409)
    body = rv.get_json()
    assert "success" in body
