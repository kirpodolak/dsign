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
        return {"accepted": True}

    playback.enqueue_play.side_effect = _play
    # Fallback if enqueue_play missing would call play — keep stub.
    playback.play.side_effect = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("sync play"))

    engine.evaluate_and_apply()

    assert release_order == ["remove", "play"]
    playback.enqueue_play.assert_called_once_with(3, source="schedule", rule_id=7)
    playback.play.assert_not_called()
    playback.stop.assert_not_called()


def test_schedule_engine_skips_remove_in_request_context(tmp_path):
    """HTTP toggle must keep ORM objects for JSON response after evaluate."""
    from datetime import time as dt_time

    from flask import Flask

    from dsign.extensions import db, _shutdown_session
    import dsign.models  # noqa: F401
    from dsign.models import Playlist, PlaybackStatus, ScheduleRule
    from dsign.services.schedule_engine import ScheduleEngine
    from dsign.services.schedule_service import ScheduleService

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.teardown_appcontext(_shutdown_session)

    with app.app_context():
        db.create_all()
        pl = Playlist(name="P")
        db.session.add(pl)
        db.session.commit()
        rule = ScheduleRule(
            playlist_id=pl.id,
            days_of_week=127,
            start_time=dt_time(0, 0),
            end_time=dt_time(23, 59),
            repeat_type="weekly",
            enabled=True,
            priority=5,
        )
        db.session.add(rule)
        db.session.add(
            PlaybackStatus(
                id=1,
                status="playing",
                source="schedule",
                playlist_id=pl.id,
                rule_id=None,
            )
        )
        db.session.commit()
        row = db.session.get(PlaybackStatus, 1)
        row.rule_id = rule.id
        db.session.commit()
        rid = rule.id

    with app.test_request_context(f"/api/schedule/rules/{rid}/toggle", method="PATCH"):
        svc = ScheduleService(db.session)
        rule = svc.toggle_rule(rid)
        remove_calls = {"n": 0}

        playback = MagicMock()
        playback._app = app
        playback.db_session = db
        playback.enqueue_play = MagicMock(return_value={"accepted": True})
        playback.enqueue_stop = MagicMock(return_value=True)
        playback.play = MagicMock(return_value=True)
        playback.stop = MagicMock(return_value=True)

        engine = ScheduleEngine(playback, svc)
        # Patch release path used outside request should NOT fire while request is active.
        engine._release_db_session = lambda: remove_calls.__setitem__(
            "n", remove_calls["n"] + 100
        )
        engine.evaluate_and_apply()
        assert remove_calls["n"] == 0
        payload = svc.rule_to_dict(rule)
        assert payload["enabled"] is False
        assert payload["playlist_name"] == "P"
        playback.enqueue_stop.assert_called_once_with(source="schedule")
        playback.stop.assert_not_called()


def test_play_seq_abort_after_stop(null_logger, tmp_path, monkeypatch):
    from dsign.services.playback_play import PlaybackPlayRunner
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._stop_play_thread = MagicMock(side_effect=lambda **k: pm._bump_play_seq())
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
    pm._persist_playback_status = MagicMock()
    pm._mpv_manager.set_playback_session_active = MagicMock()
    pm._mpv_manager.set_playback_stream_opening = MagicMock()
    pm._mpv_manager._send_command = MagicMock()
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._release_db_session = MagicMock()

    def _issue(*_a, **_k):
        # Simulate Stop while loadfile waits.
        pm._bump_play_seq()
        return {"error": "success"}

    pm._issue_loadfile = _issue  # type: ignore[method-assign]

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

    class _ImmediateThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return None

    monkeypatch.setattr("dsign.services.playback_play.Thread", _ImmediateThread)
    assert PlaybackPlayRunner(pm).run(1) is False
    # Early claim writes source before loadfile (blocks schedule races). Stop during
    # loadfile must abort _commit_play — no second persist after that.
    assert pm._persist_playback_status.call_count == 1
    claim = pm._persist_playback_status.call_args_list[0].kwargs
    assert claim["playlist_id"] == 1
    assert claim["status"] == "playing"
    assert claim["source"] == "manual"
    assert claim["clear_rule"] is True


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
