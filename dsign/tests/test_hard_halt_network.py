"""ytdl open looks idle-active — soft prepare must still hard-stop mpv."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_mpv_needs_hard_halt_for_ytdl_path(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager._playback_stream_opening = False
    pm._mpv_get_light = MagicMock(return_value="ytdl://https://rutube.ru/x")
    assert pm._mpv_needs_hard_halt() is True


def test_mpv_needs_hard_halt_for_stream_opening(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager._playback_stream_opening = True
    pm._mpv_get_light = MagicMock(return_value=True)  # idle
    assert pm._mpv_needs_hard_halt() is True


def test_prepare_halts_when_ytdl_even_if_idle_active(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    calls = []

    def _send(cmd, **_kw):
        calls.append(list(cmd["command"]))
        return {"error": "success"}

    pm._mpv_manager._send_command = _send  # type: ignore[method-assign]
    pm._mpv_manager._playback_stream_opening = False
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: (
            True if prop == "idle-active" else "ytdl://https://vk.com/video"
        )
    )
    pm._mpv_showing_idle_logo = MagicMock(return_value=False)  # type: ignore
    pm._prepare_mpv_for_new_play()
    assert ["stop"] in calls


def test_mpv_has_active_media_true_for_opening_ytdl(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager._playback_stream_opening = False
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: (
            True if prop == "idle-active" else "ytdl://https://rutube.ru/x"
        )
    )
    assert pm._mpv_has_active_media() is True


def test_invalidate_does_not_bump_playback_run_id(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    run0 = int(pm._playback_run_id)
    pm.invalidate_in_flight_play()
    assert int(pm._playback_run_id) == run0
    assert pm._stop_event.is_set()
    pm._mpv_manager.set_playback_stream_opening.assert_called_with(False)
