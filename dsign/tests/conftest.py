"""Shared pytest fixtures for dsign unit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock

import pytest
from flask import Flask

from fake_mpv_ipc import FakeMpvIpcServer, default_echo_handler


@pytest.fixture
def fake_mpv_socket(tmp_path: Path):
    """Running fake mpv IPC server; yields (socket_path, server)."""
    sock_path = str(tmp_path / "mpv.sock")
    server = FakeMpvIpcServer(sock_path)
    server.set_handler(default_echo_handler)
    server.start()
    try:
        yield sock_path, server
    finally:
        server.stop()


@pytest.fixture
def null_logger():
    class _Logger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    return _Logger()


@pytest.fixture
def schedule_db(tmp_path: Path) -> Tuple[Flask, Any, Any, Any]:
    """In-memory Flask app + DB with admin user and one playlist."""
    from dsign.extensions import bcrypt, csrf, db, login_manager
    import dsign.models  # noqa: F401
    from dsign.models import Playlist, User

    app = Flask(__name__)
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    app.config.update(
        TESTING=True,
        SECRET_KEY="pytest-secret-key",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        WTF_CSRF_ENABLED=True,
        UPLOAD_FOLDER=str(media_dir),
    )

    db.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def _load_user(user_id: str):
        return db.session.get(User, int(user_id))

    with app.app_context():
        db.create_all()
        user = User(username="admin", is_admin=True)
        user.set_password("secret")
        playlist = Playlist(name="Pytest Playlist")
        db.session.add(user)
        db.session.add(playlist)
        db.session.commit()
        yield app, db.session, user, playlist
        db.session.remove()


@pytest.fixture
def api_client(schedule_db, monkeypatch):
    """Flask test client with schedule API + auth; Bearer token configured."""
    from dsign.routes import create_blueprints
    from dsign.routes.api.api_routes import init_api_routes
    from dsign.routes.auth_routes import auth_bp
    from dsign.services.api_token_auth import configure_api_csrf_auth
    from dsign.services.schedule_service import ScheduleService

    app, session, user, playlist = schedule_db
    monkeypatch.setenv("DSIGN_API_TOKEN", "test-bearer-token")

    playback = MagicMock()
    playback.health_check.return_value = {"mpv_ready": True}
    playback._schedule_engine = None
    playback.play.return_value = True
    playback.stop.return_value = True
    playback.enqueue_play.return_value = {
        "accepted": True,
        "playlist_id": playlist.id,
        "source": "manual",
        "rule_id": None,
        "start_index": 0,
    }
    playback.enqueue_schedule_evaluate.return_value = True
    playback.return_to_schedule.return_value = True

    settings_svc = MagicMock()
    settings_svc.load_settings.return_value = {"timezone": "UTC"}
    settings_svc.get_current_settings.return_value = {
        "timezone": "UTC",
        "profile_id": None,
    }
    settings_svc.update_mpv_settings.return_value = True

    services: Dict[str, Any] = {
        "schedule_service": ScheduleService(session, settings_service=settings_svc),
        "playback_service": playback,
        "file_service": MagicMock(),
        "socket_service": MagicMock(),
        "settings_service": settings_svc,
    }

    _main_bp, api_bp = create_blueprints()
    init_api_routes(api_bp, services)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    configure_api_csrf_auth(app)

    client = app.test_client()
    return client, app, user, playlist
