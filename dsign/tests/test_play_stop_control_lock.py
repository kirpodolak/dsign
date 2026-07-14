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
