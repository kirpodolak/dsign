"""Async Stop must not force-restart mpv after a newer Play owns playback_run_id."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService
from dsign.services.playlist_management import PlaylistManager


def test_stop_impl_skips_mpv_cleanup_when_run_superseded(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager.set_playback_session_active = MagicMock()
    pm._mpv_manager.set_playback_stream_opening = MagicMock()
    pm._cancel_content_cache_prefetches = MagicMock(
        side_effect=lambda: setattr(pm, "_playback_run_id", 2)
    )
    pm._set_playback_active_marker = MagicMock()
    pm._persist_playback_status = MagicMock()
    pm._stop_play_thread = MagicMock(
        side_effect=lambda **_k: setattr(pm, "_playback_run_id", 1)
    )
    pm._halt_mpv_playback = MagicMock(return_value=True)  # type: ignore[method-assign]
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._mpv_manager._force_restart_mpv_for_hung_recovery = MagicMock(return_value=True)
    pm._mpv_content_still_on_air = MagicMock(return_value=True)
    pm._mpv_loop_props_on = MagicMock(return_value=True)
    pm._enqueue_idle_logo_retry = MagicMock()  # type: ignore[method-assign]
    pm._acquire_play_handoff = MagicMock(return_value=True)  # type: ignore[method-assign]
    pm._release_play_handoff = MagicMock()  # type: ignore[method-assign]

    row = MagicMock()
    row.playlist_id = 3
    pm.db_session.query.return_value.get.return_value = row
    pm._playback_run_id = 0

    assert pm._stop_impl(source="manual", show_idle_logo=True) is True
    pm._halt_mpv_playback.assert_not_called()
    pm._mpv_manager._force_restart_mpv_for_hung_recovery.assert_not_called()
    pm._logo_manager.display_idle_logo.assert_not_called()


def test_stop_impl_skips_teardown_when_stop_generation_stale(null_logger, tmp_path):
    """Async Stop must not call _stop_play_thread after Play bumped run_id."""
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._playback_run_id = 9
    pm._acquire_play_handoff = MagicMock(return_value=True)  # type: ignore[method-assign]
    pm._release_play_handoff = MagicMock()  # type: ignore[method-assign]
    pm._stop_play_thread = MagicMock()  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()  # type: ignore[method-assign]
    pm._halt_mpv_playback = MagicMock(return_value=True)  # type: ignore[method-assign]

    assert pm._stop_impl(source="manual", stop_generation=4, show_idle_logo=True) is True
    pm._stop_play_thread.assert_not_called()
    pm._acquire_play_handoff.assert_not_called()
    pm._persist_playback_status.assert_not_called()


def test_enqueue_stop_bumps_run_id_before_invalidate():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager = MagicMock()
    order = []

    def _bump():
        order.append("bump")
        return 3

    def _inv():
        order.append("invalidate")

    svc._playlist_manager._bump_playback_run_id.side_effect = _bump
    svc._playlist_manager.invalidate_in_flight_play.side_effect = _inv
    svc.stop = MagicMock(return_value=True)

    assert PlaybackService.enqueue_stop(svc, source="manual") is True
    assert order == ["bump", "invalidate"]
    import time

    time.sleep(0.05)
    svc.stop.assert_called_once_with(source="manual", stop_generation=3)


def test_enqueue_play_does_not_bump_playback_run_id():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager = MagicMock()
    svc._playlist_manager.claim_playback_intent = MagicMock()
    svc.play = MagicMock(return_value=True)

    PlaybackService.enqueue_play(svc, 2, source="manual")
    svc._playlist_manager._bump_playback_run_id.assert_not_called()


def test_clear_ghost_playing_after_slideshow_exit(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._is_playback_run_current = MagicMock(return_value=True)  # type: ignore[method-assign]
    pm._mpv_has_active_media = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()
    pm.socketio = None
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 9
    pm.db_session.query.return_value.get.return_value = row

    pm._clear_ghost_playing_after_slideshow_exit(9, 3)
    pm._persist_playback_status.assert_called_once()
    args, kwargs = pm._persist_playback_status.call_args
    assert kwargs.get("status") == "idle"


def test_clear_ghost_skipped_when_run_superseded(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._is_playback_run_current = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()

    pm._clear_ghost_playing_after_slideshow_exit(9, 3)
    pm._persist_playback_status.assert_not_called()
