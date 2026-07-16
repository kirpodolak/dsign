"""Full ytdl failure cycle must idle instead of babysitting 'playing' for minutes."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_all_network_fail_without_last_good_goes_idle(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._last_good_item_index = None
    pm.get_network_playback_health = MagicMock(return_value={"consecutive_ytdl_failures": 3})
    pm._prepare_mpv_for_new_play = MagicMock()
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    persisted = []
    pm._persist_playback_status = lambda **kw: persisted.append(dict(kw))  # type: ignore
    pm.socketio = MagicMock()

    assert pm._handle_all_network_items_failed_cycle(playlist_id=5, items_count=1) is None
    assert pm._stop_event.is_set()
    assert persisted[-1]["status"] == "idle"
    assert persisted[-1]["source"] == "idle"
    pm._logo_manager.display_idle_logo.assert_called()


def test_ytdl_open_timeout_defaults_are_capped(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._consecutive_ytdl_failures = 0
    pm._post_mpv_restart_until = 0.0
    assert pm._ytdl_open_timeout_sec(120.0) == 120.0
    assert pm._ytdl_open_timeout_sec(55.0) == 55.0
    pm._consecutive_ytdl_failures = 1
    assert pm._ytdl_open_timeout_sec(120.0) == 90.0
    pm._consecutive_ytdl_failures = 2
    assert pm._ytdl_open_timeout_sec(120.0) == 45.0
