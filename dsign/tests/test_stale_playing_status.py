"""Ghost status: DB playing while only idle logo is on screen."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_mpv_has_active_media_ignores_idle_logo(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: {
            "idle-active": False,
            "path": "/home/dsign/dsign/static/images/placeholder.jpg",
        }.get(prop)
    )
    assert pm._mpv_has_active_media() is False
    assert pm._mpv_showing_idle_logo() is True


def test_remote_controllable_false_for_logo_only(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 3
    pm.db_session.query.return_value.first.return_value = row
    pm._play_thread = None
    pm._active_playlist_id = None
    pm._mpv_manager._playback_session_active = True
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: {
            "idle-active": False,
            "path": "/var/lib/dsign/idle_logo.png",
        }.get(prop)
    )
    ok, snap = pm._remote_playback_controllable()
    assert ok is False
    assert snap["idle_logo"] is True


def test_get_status_reports_stale_playing_as_idle(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 9
    row.source = "schedule"
    row.rule_id = 4
    row.previous_source = None
    row.previous_rule_id = None
    row.previous_playlist_id = None
    pm.db_session.query.return_value.get.return_value = row
    pm._play_thread = None
    pm._play_start_mono = 0.0
    pm._mpv_manager._playback_session_active = False
    pm._mpv_manager._current_settings = {}
    pm._content_cache = None
    pm._get_loop_position_snapshot = MagicMock(return_value=(None, 0))
    pm._get_mpv_playback_snapshot = MagicMock(
        return_value={"time_pos": None, "duration": None, "is_network": False, "mpv_responsive": True}
    )
    pm.get_network_playback_health = MagicMock(return_value={})
    pm._get_current_media_label = MagicMock(return_value="should-hide")
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: {
            "loop-playlist": "no",
            "path": "/home/dsign/dsign/static/images/placeholder.jpg",
        }.get(prop)
    )

    st = pm.get_status()
    assert st["stale_playing"] is True
    assert st["status"] == "idle"
    assert st["source"] == "idle"
    assert st["playlist_id"] is None
    assert st["current_media"] is None


def test_get_status_not_stale_during_play_start_grace(null_logger, tmp_path):
    import time

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    row = MagicMock()
    row.status = "playing"
    row.playlist_id = 9
    row.source = "manual"
    row.rule_id = None
    row.previous_source = None
    row.previous_rule_id = None
    row.previous_playlist_id = None
    pm.db_session.query.return_value.get.return_value = row
    pm._play_thread = None
    pm._play_start_mono = time.monotonic()
    pm._mpv_manager._playback_session_active = False
    pm._mpv_manager._current_settings = {}
    pm._content_cache = None
    pm._get_loop_position_snapshot = MagicMock(return_value=(None, 0))
    pm._get_mpv_playback_snapshot = MagicMock(
        return_value={"time_pos": None, "duration": None, "is_network": False, "mpv_responsive": True}
    )
    pm.get_network_playback_health = MagicMock(return_value={})
    pm._get_current_media_label = MagicMock(return_value="loading")
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: {
            "loop-playlist": "no",
            "path": "/home/dsign/dsign/static/images/placeholder.jpg",
        }.get(prop)
    )

    st = pm.get_status()
    assert st["stale_playing"] is False
    assert st["status"] == "playing"


def test_idle_logo_retry_cancelled_by_play_epoch(null_logger, tmp_path):
    import time

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._app = None
    pm._halt_mpv_playback = MagicMock(return_value=True)
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm.mark_play_starting()
    pm._enqueue_idle_logo_retry()
    time.sleep(0.05)
    pm._halt_mpv_playback.assert_not_called()
    pm._logo_manager.display_idle_logo.assert_not_called()


def test_prepare_mpv_for_new_play_skips_stop_when_idle(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    calls = []

    def _send(cmd, **_kw):
        calls.append(list(cmd["command"]))
        return {"error": "success"}

    pm._mpv_manager._send_command = _send  # type: ignore[method-assign]
    pm._mpv_manager._playback_stream_opening = False
    # Truly idle: no path, idle-active true — soft clear loops only.
    pm._mpv_get_light = MagicMock(
        side_effect=lambda prop, **_k: (True if prop == "idle-active" else None)
    )
    pm._mpv_showing_idle_logo = MagicMock(return_value=False)  # type: ignore
    pm._mpv_has_active_media = MagicMock(return_value=False)  # type: ignore
    pm._prepare_mpv_for_new_play()
    assert ["stop"] not in calls
    assert ["set_property", "loop-file", "no"] in calls


def test_claim_playback_intent_clears_rule_for_manual(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    persisted = []
    pm._persist_playback_status = lambda **kw: persisted.append(dict(kw))  # type: ignore
    pm.claim_playback_intent(5, source="manual", rule_id=99)
    assert persisted == [
        {
            "playlist_id": 5,
            "status": "playing",
            "source": "manual",
            "rule_id": None,
            "clear_rule": True,
        }
    ]


def test_play_error_superseded_skips_idle_wipe(null_logger, tmp_path):
    from dsign.services.playback_play import PlaybackPlayRunner

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm.mark_play_starting = MagicMock()
    pm._stop_play_thread = MagicMock()
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._prune_media_backoff = MagicMock()
    pm._begin_play_seq = MagicMock(return_value=1)
    pm._is_play_seq_current = MagicMock(return_value=False)
    pm._set_playback_active_marker = MagicMock()
    pm._persist_playback_status = MagicMock()
    pm.db_session.query.return_value.get.side_effect = RuntimeError("boom")

    runner = PlaybackPlayRunner(pm)
    assert runner.run(1, source="manual") is False
    # Must not wipe winner via idle persist after superseded failure.
    for call in pm._persist_playback_status.call_args_list:
        assert call.kwargs.get("status") != "idle"
