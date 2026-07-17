"""claim_playback_intent must not leave ghost playing when play() never starts."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService
from dsign.services.playlist_management import PlaylistManager
from dsign.services.schedule_engine import ScheduleEngine


def test_rollback_claimed_play_clears_idle_when_no_threads(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._any_play_threads_alive = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()
    pm.socketio = None
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 8
    pm.db_session.query.return_value.get.return_value = row

    pm.rollback_claimed_play(8, reason="test")
    pm._persist_playback_status.assert_called_once()
    kwargs = pm._persist_playback_status.call_args.kwargs
    assert kwargs.get("status") == "idle"


def test_enqueue_play_rolls_back_when_play_returns_false():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager = MagicMock()
    svc._playlist_manager.claim_playback_intent = MagicMock()
    svc._playlist_manager.rollback_claimed_play = MagicMock()
    svc.play = MagicMock(return_value=False)

    PlaybackService.enqueue_play(svc, 8, source="manual")

    import time

    time.sleep(0.05)
    svc._playlist_manager.rollback_claimed_play.assert_called_once_with(
        8, reason="play_async_returned_false"
    )


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
