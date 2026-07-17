"""Schedule A2 loop-file=inf must not keep playing after Stop."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService
from dsign.services.playlist_management import PlaylistManager


def test_mpv_loop_props_on_detects_loop_file(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: "inf" if prop == "loop-file" else "no"
    )
    assert pm._mpv_loop_props_on() is True


def test_mpv_content_still_on_air_for_looping_local(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_has_active_media = MagicMock(return_value=False)  # type: ignore
    pm._mpv_loop_props_on = MagicMock(return_value=True)  # type: ignore
    pm._mpv_path_is_idle_logo = MagicMock(return_value=False)  # type: ignore
    pm._mpv_get_light = MagicMock(return_value="/var/lib/dsign/media/clip.mp4")
    assert pm._mpv_content_still_on_air() is True


def test_stop_force_restarts_when_content_still_on_air(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager.set_playback_session_active = MagicMock()
    pm._mpv_manager.set_playback_stream_opening = MagicMock()
    pm._mpv_manager._playback_stream_opening = False
    pm._mpv_manager._force_restart_mpv_for_hung_recovery = MagicMock(return_value=True)
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._set_playback_active_marker = MagicMock()
    pm._clear_current_media_label = MagicMock()
    pm._clear_loop_position = MagicMock()
    pm._reset_stall_tracking = MagicMock()
    pm._clear_stall_restart_pending = MagicMock()
    pm._halt_mpv_playback = MagicMock(return_value=True)  # type: ignore[method-assign]
    # Halt claimed success but A2 loop kept the file on air.
    pm._mpv_content_still_on_air = MagicMock(side_effect=[True, False])  # type: ignore
    pm._mpv_loop_props_on = MagicMock(return_value=True)  # type: ignore
    pm._enqueue_idle_logo_retry = MagicMock()  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()  # type: ignore[method-assign]
    pm._stop_play_thread = MagicMock()  # type: ignore[method-assign]
    row = MagicMock()
    row.playlist_id = 1
    pm.db_session.query.return_value.get.return_value = row

    assert pm._stop_impl(source="manual", show_idle_logo=True) is True
    pm._mpv_manager._force_restart_mpv_for_hung_recovery.assert_called_once()


def test_enqueue_stop_halts_before_async_thread():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager = MagicMock()
    order = []

    def _bump():
        order.append("bump")

    def _inv():
        order.append("invalidate")

    def _halt(**_k):
        order.append("halt")
        return True

    svc._playlist_manager._bump_playback_run_id.side_effect = _bump
    svc._playlist_manager.invalidate_in_flight_play.side_effect = _inv
    svc._playlist_manager._halt_mpv_playback.side_effect = _halt
    svc.stop = MagicMock(return_value=True)

    assert PlaybackService.enqueue_stop(svc, source="manual") is True
    assert order == ["bump", "invalidate", "halt"]
