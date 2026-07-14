"""SQLAlchemy pool / schedule session release (QueuePool exhaustion fix)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy.pool import NullPool


def test_configure_sqlite_engine_options_uses_null_pool_for_file(tmp_path: Path):
    from dsign.extensions import configure_sqlite_engine_options, db
    import dsign.models  # noqa: F401

    db_path = tmp_path / "pool.db"
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    configure_sqlite_engine_options(app)
    assert app.config["SQLALCHEMY_ENGINE_OPTIONS"]["poolclass"] is NullPool
    db.init_app(app)
    with app.app_context():
        assert isinstance(db.engine.pool, NullPool)


def test_configure_sqlite_engine_options_skips_memory():
    from dsign.extensions import configure_sqlite_engine_options

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    configure_sqlite_engine_options(app)
    assert "SQLALCHEMY_ENGINE_OPTIONS" not in app.config or not app.config.get(
        "SQLALCHEMY_ENGINE_OPTIONS"
    )


def test_schedule_engine_releases_session_before_play():
    from dsign.services.schedule_engine import ScheduleEngine

    playback = MagicMock()
    playback._app = None

    session = MagicMock(spec=["query", "remove", "add", "commit", "rollback"])
    playback.db_session = session

    rule = MagicMock()
    rule.id = 7
    rule.playlist_id = 3

    schedule = MagicMock()
    schedule.find_active_rule_candidates.return_value = [rule]

    engine = ScheduleEngine(playback, schedule, settings_service=None, logger=MagicMock())

    # Pretend current playback is idle so a play action is planned.
    row = MagicMock()
    row.source = "idle"
    row.rule_id = None
    row.playlist_id = None
    session.query.return_value.get.return_value = row

    release_order: list[str] = []

    def _remove():
        release_order.append("remove")

    session.remove.side_effect = _remove

    def _play(*_a, **_k):
        release_order.append("play")
        return True

    playback.play.side_effect = _play

    engine.evaluate_and_apply()

    assert release_order == ["remove", "play"]
    playback.play.assert_called_once_with(3, source="schedule", rule_id=7)
    playback.stop.assert_not_called()


def test_playback_play_releases_db_before_network_loadfile(null_logger, tmp_path, monkeypatch):
    from dsign.services.playback_play import PlaybackPlayRunner
    from dsign.services.playlist_management import PlaylistManager

    releases: list[str] = []
    load_calls: list[str] = []

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._release_db_session = lambda: releases.append("release")  # type: ignore[method-assign]
    pm._stop_play_thread = MagicMock()
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._prune_media_backoff = MagicMock()
    pm._set_playback_active_marker = MagicMock()
    pm._playlist_playback_mode = MagicMock(return_value="manual")
    pm._resolve_playlist_item_path = MagicMock(
        return_value={
            "path": "ytdl://https://example.com/v",
            "is_video": True,
            "is_audio": False,
            "key": "ext-1",
        }
    )
    pm._media_label_for_file_name = MagicMock(return_value="v")
    pm._item_media_label = MagicMock(return_value="v")
    pm._set_loop_position = MagicMock()
    pm._set_current_media_label = MagicMock()
    pm._apply_mpv_http_headers = MagicMock(return_value=(None, {}))
    pm._apply_mpv_ytdl_options = MagicMock()
    pm._mpv_loadfile_command = MagicMock(return_value=["loadfile", "ytdl://x", "replace"])
    pm._ytdl_loadfile_ipc_timeout_sec = MagicMock(return_value=5.0)

    def _issue(*_a, **_k):
        load_calls.append("load")
        assert "release" in releases, "DB must be released before loadfile"
        return {"error": "success"}

    pm._issue_loadfile = _issue  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()
    pm._mpv_manager.set_playback_session_active = MagicMock()
    pm._mpv_manager.set_playback_stream_opening = MagicMock()
    pm._mpv_manager._send_command = MagicMock()
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._run_manual_slideshow_loop = MagicMock()

    playlist = MagicMock()
    playlist.id = 1
    playlist.name = "Net"
    file_row = MagicMock()
    file_row.file_name = "ext-1"
    file_row.order = 0
    file_row.duration = 0
    file_row.muted = False
    playlist.files = [file_row]

    pm.db_session.query.return_value.get.return_value = playlist
    pm.db_session.query.return_value.filter_by.return_value.first.return_value = None

    # Thread start should be immediate; avoid hanging.
    class _ImmediateThread:
        def __init__(self, target=None, args=None, kwargs=None, daemon=None):
            self._target = target
            self._args = args or ()
            self._kwargs = kwargs or {}

        def start(self):
            # Do not run slideshow; just mark started.
            return None

    monkeypatch.setattr("dsign.services.playback_play.Thread", _ImmediateThread)

    runner = PlaybackPlayRunner(pm)
    assert runner.run(1, source="schedule", rule_id=2) is True
    assert releases[0] == "release"
    assert load_calls == ["load"]
    pm._persist_playback_status.assert_called()
