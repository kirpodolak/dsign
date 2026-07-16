"""Stop→Play / return must not abort ytdl on quiet IPC or inherit fail-fast streak."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_mark_play_starting_resets_ytdl_streak_and_backoff(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._consecutive_ytdl_failures = 5
    pm._media_backoff = {"ext-1": {"failures": 3, "next_try_monotonic": 9999.0}}
    pm.mark_play_starting()
    assert pm._consecutive_ytdl_failures == 0
    assert pm._media_backoff == {}


def test_cold_ytdl_open_timeout_is_generous(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._consecutive_ytdl_failures = 0
    pm._post_mpv_restart_until = 0.0
    assert pm._ytdl_open_timeout_sec(120.0) == 120.0
    pm._consecutive_ytdl_failures = 2
    assert pm._ytdl_open_timeout_sec(120.0) == 45.0


def test_network_loadfile_quiet_ipc_does_not_abort_play(null_logger, tmp_path):
    """ytdl often omits IPC success; play() must preload and let ensure() finish."""
    from dsign.services.playback_play import PlaybackPlayRunner

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    runner = PlaybackPlayRunner(pm)

    pf = MagicMock()
    pf.file_name = "ext-9"
    pf.order = 0
    pf.duration = 0
    pf.muted = False

    playlist = MagicMock()
    playlist.name = "net"
    playlist.id = 7
    playlist.files = [pf]

    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PlaylistProfile" in name or "Assignment" in name:
            q.filter_by.return_value.first.return_value = None
            q.get.return_value = None
        else:
            q.get.return_value = playlist
        return q

    pm.db_session.query.side_effect = _query
    pm._resolve_playlist_item_path = MagicMock(  # type: ignore[method-assign]
        return_value={
            "path": "ytdl://https://rutube.ru/video/abc",
            "is_video": True,
            "is_audio": False,
            "key": "ext-9",
            "http_headers": {},
        }
    )
    pm._playlist_playback_mode = MagicMock(return_value="manual_slideshow")  # type: ignore[method-assign]
    pm.mark_play_starting = MagicMock()
    pm._stop_play_thread = MagicMock()
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._prune_media_backoff = MagicMock()
    pm._begin_play_seq = MagicMock(return_value=1)
    pm._is_play_seq_current = MagicMock(return_value=True)
    pm._set_playback_active_marker = MagicMock()
    pm._persist_playback_status = MagicMock()
    pm._release_db_session = MagicMock()
    pm._mpv_showing_idle_logo = MagicMock(return_value=True)
    pm._halt_mpv_playback = MagicMock(return_value=True)
    pm._apply_mpv_http_headers = MagicMock(return_value=({}, {}))
    pm._apply_mpv_ytdl_options = MagicMock()
    pm._mpv_loadfile_command = MagicMock(
        return_value=["loadfile", "ytdl://https://rutube.ru/video/abc", "replace"]
    )
    pm._issue_loadfile = MagicMock(return_value=None)  # quiet IPC
    pm._issue_ytdl_loadfile = MagicMock()
    pm._commit_play = MagicMock(return_value=True)
    pm._set_current_media_label = MagicMock()
    pm._set_loop_position = MagicMock()
    pm._item_media_label = MagicMock(return_value="clip")
    pm._get_current_media_label = MagicMock(return_value="clip")
    pm._media_label_for_file_name = MagicMock(return_value="clip")
    pm._logo_manager = MagicMock()
    pm.socketio = None
    pm._mpv_manager = MagicMock()
    pm._mpv_manager._send_command = MagicMock(return_value={"error": "success"})
    pm._mpv_manager.set_playback_stream_opening = MagicMock()
    pm._mpv_manager.set_playback_session_active = MagicMock()

    ok = runner.run(7, source="manual")
    assert ok is True
    assert pm._preloaded_load_cmd is not None
    assert pm._preloaded_load_ipc_ok is False
    pm._issue_ytdl_loadfile.assert_called()
    pm._halt_mpv_playback.assert_called()
    pm._commit_play.assert_called()
