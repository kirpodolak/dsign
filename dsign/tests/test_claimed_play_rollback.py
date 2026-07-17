"""claim_playback_intent must not leave ghost playing when play() never starts."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService
from dsign.services.playlist_management import PlaylistManager
from dsign.services.schedule_engine import ScheduleEngine


def test_rollback_claimed_play_keeps_manual_lock(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._any_play_threads_alive = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()
    pm.socketio = None
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 8
    pm.db_session.query.return_value.get.return_value = row

    pm.rollback_claimed_play(8, reason="test", claim_source="manual")
    pm._persist_playback_status.assert_called_once()
    kwargs = pm._persist_playback_status.call_args.kwargs
    assert kwargs.get("status") == "stopped"
    assert kwargs.get("source") == "manual"
    assert kwargs.get("playlist_id") == 8


def test_rollback_claimed_play_schedule_goes_idle(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._any_play_threads_alive = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()
    pm.socketio = None
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 5
    pm.db_session.query.return_value.get.return_value = row

    pm.rollback_claimed_play(5, reason="test", claim_source="schedule")
    kwargs = pm._persist_playback_status.call_args.kwargs
    assert kwargs.get("status") == "idle"
    assert kwargs.get("source") == "idle"
    assert kwargs.get("playlist_id") is None


def test_enqueue_play_rolls_back_when_play_returns_false():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._schedule_play_cooldown_until = 0.0
    svc._playlist_manager = MagicMock()
    svc._playlist_manager.claim_playback_intent = MagicMock()
    svc._playlist_manager.rollback_claimed_play = MagicMock()
    svc.play = MagicMock(return_value=False)

    PlaybackService.enqueue_play(svc, 8, source="manual")

    import time

    time.sleep(0.05)
    svc._playlist_manager.rollback_claimed_play.assert_called_once_with(
        8, reason="play_async_returned_false", claim_source="manual"
    )


def test_enqueue_play_schedule_skipped_on_cooldown():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_warning = MagicMock()
    svc._schedule_play_cooldown_until = 1e18  # far future
    svc._playlist_manager = MagicMock()

    out = PlaybackService.enqueue_play(svc, 5, source="schedule", rule_id=9)
    assert out["accepted"] is False
    assert out["reason"] == "schedule_play_cooldown"
    svc._playlist_manager.claim_playback_intent.assert_not_called()
    svc._playlist_manager.mark_play_starting.assert_not_called()


def test_enqueue_play_schedule_failure_arms_cooldown():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._schedule_play_cooldown_until = 0.0
    svc._playlist_manager = MagicMock()
    svc._playlist_manager.claim_playback_intent = MagicMock()
    svc._playlist_manager.rollback_claimed_play = MagicMock()
    svc.play = MagicMock(return_value=False)

    PlaybackService.enqueue_play(svc, 5, source="schedule", rule_id=1)

    import time

    time.sleep(0.05)
    assert svc._schedule_play_cooldown_until > time.monotonic()
    svc._playlist_manager.rollback_claimed_play.assert_called_once_with(
        5, reason="play_async_returned_false", claim_source="schedule"
    )


def _svc_for_clear_stale(row):
    from contextlib import nullcontext

    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = MagicMock()
    svc._app.app_context.return_value = nullcontext()
    svc._log_warning = MagicMock()
    svc.socketio = None
    svc._playlist_manager = MagicMock()
    svc._mpv_manager = MagicMock()
    # Avoid MagicMock.session child hijacking getattr(..., "session", db_session).
    session = MagicMock(spec=["query"])
    session.query.return_value.get.return_value = row
    svc.db_session = session
    return svc


def test_clear_stale_playing_keeps_manual_lock():
    row = MagicMock()
    row.source = "manual"
    row.playlist_id = 5
    svc = _svc_for_clear_stale(row)

    PlaybackService._clear_stale_playing_status(svc)
    kwargs = svc._playlist_manager._persist_playback_status.call_args.kwargs
    assert kwargs.get("status") == "stopped"
    assert kwargs.get("source") == "manual"
    assert kwargs.get("playlist_id") == 5


def test_clear_stale_playing_schedule_goes_idle():
    row = MagicMock()
    row.source = "schedule"
    row.playlist_id = 6
    svc = _svc_for_clear_stale(row)

    PlaybackService._clear_stale_playing_status(svc)
    kwargs = svc._playlist_manager._persist_playback_status.call_args.kwargs
    assert kwargs.get("status") == "idle"
    assert kwargs.get("source") == "idle"
    assert kwargs.get("playlist_id") is None


def test_schedule_engine_uses_enqueue_play():
    playback = MagicMock()
    playback.enqueue_play = MagicMock(return_value={"accepted": True})
    playback.enqueue_stop = MagicMock(return_value=True)
    engine = ScheduleEngine(playback, MagicMock(), logger=MagicMock())
    engine._apply_action(("play", 6, 2))
    playback.enqueue_play.assert_called_once_with(6, source="schedule", rule_id=2)
    playback.play.assert_not_called()


def test_mpv_showing_idle_logo_when_idle_active_and_empty_path(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: True if prop == "idle-active" else ""
    )
    assert pm._mpv_showing_idle_logo() is True
