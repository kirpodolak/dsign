"""Stop must win races against late schedule play persist."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_commit_play_aborts_when_stop_bumps_seq(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    persisted = []
    pm._persist_playback_status = lambda **kw: persisted.append(dict(kw))  # type: ignore

    seq = pm._begin_play_seq()
    pm._bump_play_seq()  # Stop invalidates in-flight play

    ok = pm._commit_play(
        seq,
        start_thread=lambda: None,
        persist=lambda: pm._persist_playback_status(
            playlist_id=1, status="playing", source="schedule", rule_id=1
        ),
    )
    assert ok is False
    assert persisted == []


def test_stop_persist_after_commit_leaves_stopped(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager._send_command = MagicMock(return_value={"error": "success"})
    pm._mpv_manager.set_playback_session_active = MagicMock()
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._set_playback_active_marker = MagicMock()
    pm._clear_current_media_label = MagicMock()
    pm._clear_loop_position = MagicMock()
    pm._reset_stall_tracking = MagicMock()
    pm._clear_stall_restart_pending = MagicMock()

    persisted = []
    pm._persist_playback_status = lambda **kw: persisted.append(dict(kw))  # type: ignore

    row = MagicMock()
    row.playlist_id = 5
    pm.db_session.query.return_value.get.return_value = row

    seq = pm._begin_play_seq()
    assert (
        pm._commit_play(
            seq,
            start_thread=lambda: None,
            persist=lambda: pm._persist_playback_status(
                playlist_id=5, status="playing", source="schedule", rule_id=2
            ),
        )
        is True
    )

    assert pm._stop_impl(source="manual", show_idle_logo=True, update_status=True) is True
    assert persisted[-1]["status"] == "stopped"
    assert persisted[-1]["source"] == "manual"


def test_halt_mpv_playback_clears_loops_then_stops(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    calls = []

    def _send(cmd, **_kw):
        calls.append(list(cmd["command"]))
        return {"error": "success"}

    pm._mpv_manager._send_command = _send  # type: ignore[method-assign]
    assert pm._halt_mpv_playback() is True
    assert calls == [
        ["set_property", "loop-file", "no"],
        ["set_property", "loop-playlist", "no"],
        ["stop"],
    ]


def test_stop_retries_idle_logo_when_halt_fails(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._mpv_manager.set_playback_session_active = MagicMock()
    pm._mpv_manager.set_playback_stream_opening = MagicMock()
    pm._mpv_manager._playback_stream_opening = False
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._set_playback_active_marker = MagicMock()
    pm._clear_current_media_label = MagicMock()
    pm._clear_loop_position = MagicMock()
    pm._reset_stall_tracking = MagicMock()
    pm._clear_stall_restart_pending = MagicMock()
    pm._halt_mpv_playback = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._mpv_needs_hard_halt = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._mpv_content_still_on_air = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._mpv_loop_props_on = MagicMock(return_value=False)  # type: ignore[method-assign]
    pm._mpv_manager._force_restart_mpv_for_hung_recovery = MagicMock(return_value=False)
    pm._enqueue_idle_logo_retry = MagicMock()  # type: ignore[method-assign]
    pm._persist_playback_status = MagicMock()  # type: ignore[method-assign]
    pm._stop_play_thread = MagicMock()  # type: ignore[method-assign]
    row = MagicMock()
    row.playlist_id = 1
    pm.db_session.query.return_value.get.return_value = row

    assert pm._stop_impl(source="manual", show_idle_logo=True) is True
    pm._enqueue_idle_logo_retry.assert_called_once()
    pm._mpv_manager._force_restart_mpv_for_hung_recovery.assert_called_once()
