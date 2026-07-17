"""Integration-style unit tests for playback recovery flows (backlog T-REC)."""

from __future__ import annotations

from contextlib import nullcontext
from threading import Event, Lock
from unittest.mock import MagicMock

import pytest

from dsign.services.mpv_ipc_session import MPVIPCClosedError
from dsign.services.mpv_management import MPVManager
from dsign.services.playback_service import PlaybackService
from dsign.services.playlist_management import PlaylistManager
from dsign.services.recovery_queue import RecoveryJobKind, RecoveryQueue


def _make_playlist_manager(null_logger, tmp_path) -> PlaylistManager:
    mpv = MagicMock()
    logo = MagicMock()
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), mpv, logo)
    return pm


def _make_recovery_service(null_logger, *, db_source: str = "schedule", db_playlist_id: int = 6) -> PlaybackService:
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = null_logger
    svc._log_info = lambda *args, **kwargs: None
    svc._log_warning = lambda *args, **kwargs: None
    svc._log_error = lambda *args, **kwargs: None
    svc._recover_lock = Lock()
    svc._recovery_queue = RecoveryQueue()
    svc._playlist_manager = MagicMock()
    # Boot grace must be off for desync-heal unit tests (real method returns bool).
    svc._playlist_manager.in_boot_grace.return_value = False
    svc._playlist_manager._play_start_mono = 0.0
    svc._mpv_manager = MagicMock()
    svc._app_context = lambda: nullcontext()
    svc._last_socket_identity = None
    svc._last_desync_recover_ts = 0.0
    svc._schedule_play_cooldown_until = 0.0
    svc._wait_after_mpv_recover = lambda: None
    svc._mpv_socket_identity = lambda: ("sock", 1)
    svc.play = MagicMock(return_value=True)
    svc.socketio = None
    svc._resolve_playlist_id_for_recovery = MagicMock(return_value=7)
    row = MagicMock()
    row.source = db_source
    row.playlist_id = db_playlist_id
    row.status = "playing"
    session = MagicMock(spec=["query"])
    session.query.return_value.get.return_value = row
    svc.db_session = session
    return svc


def test_get_resume_start_index_for_hung_recovery_prefers_last_good(null_logger, tmp_path):
    pm = _make_playlist_manager(null_logger, tmp_path)
    pm._last_good_item_index = 4
    pm._last_good_items_count = 10
    pm._loop_item_index = 1
    pm._loop_items_count = 10

    assert pm.get_resume_start_index_for_hung_recovery() == 4


def test_get_resume_start_index_for_hung_recovery_falls_back_to_loop(null_logger, tmp_path):
    pm = _make_playlist_manager(null_logger, tmp_path)
    pm._loop_item_index = 3
    pm._loop_items_count = 8

    assert pm.get_resume_start_index_for_hung_recovery() == 3


def test_consume_stall_recovery_advance_is_one_shot(null_logger, tmp_path):
    pm = _make_playlist_manager(null_logger, tmp_path)
    pm._stall_recovery_advance = True
    pm._stall_count_by_media["net-1"] = 2

    assert pm.consume_stall_recovery_advance() is True
    assert pm.consume_stall_recovery_advance() is False
    assert pm._stall_count_by_media == {}


def test_recover_after_mpv_systemd_restart_resumes_playlist(null_logger):
    svc = _make_recovery_service(null_logger)
    svc._playlist_manager.get_resume_start_index_for_hung_recovery.return_value = 2
    svc._mpv_manager.wait_for_ipc_socket_at_startup.return_value = True
    svc._mpv_manager.initialize.return_value = True
    svc._resolve_playback_resume_context = MagicMock(return_value=(7, "schedule", 42))

    ok = svc.recover_after_mpv_systemd_restart()

    assert ok is True
    svc._playlist_manager.stop.assert_called_once_with(
        show_idle_logo=False,
        update_status=False,
        preserve_stall_tracking=True,
        preserve_loop_position=True,
    )
    svc._playlist_manager.mark_post_mpv_restart.assert_called_once()
    svc._mpv_manager._reset_ipc_session.assert_called_once()
    svc.play.assert_called_once_with(
        7, start_index=2, preserve_stall_tracking=True, source="schedule", rule_id=42
    )


def test_recover_after_mpv_systemd_restart_uses_advance_index(null_logger):
    svc = _make_recovery_service(null_logger)
    svc._playlist_manager.get_resume_start_index.return_value = 5
    svc._mpv_manager.wait_for_ipc_socket_at_startup.return_value = True
    svc._mpv_manager.initialize.return_value = True
    svc._resolve_playback_resume_context = MagicMock(return_value=(7, "schedule", 3))

    ok = svc.recover_after_mpv_systemd_restart(resume_advance=True)

    assert ok is True
    svc._playlist_manager.get_resume_start_index.assert_called_once_with(advance=True)
    svc.play.assert_called_once_with(
        7, start_index=5, preserve_stall_tracking=True, source="schedule", rule_id=3
    )


def test_recover_queued_when_lock_held(null_logger):
    svc = _make_recovery_service(null_logger)
    assert svc._recover_lock.acquire(blocking=False)
    try:
        ok = svc.recover_after_mpv_systemd_restart()
    finally:
        pass

    assert ok is False
    svc.play.assert_not_called()
    assert RecoveryJobKind.MPV_SYSTEMD.value in svc._recovery_queue.pending_kinds()

    svc._recover_lock.release()
    svc._process_next_queued_recovery()

    svc.play.assert_called_once()


def test_hung_recovery_invokes_post_restart_callback(null_logger, monkeypatch):
    mgr = MPVManager(logger=null_logger, socketio=None, upload_folder="/tmp", mpv_socket="/tmp/mpv.sock")
    called: list[bool] = []
    mgr.set_post_restart_callback(lambda: called.append(True))
    mgr.set_playback_session_active(True)
    monkeypatch.setattr(mgr, "_restart_systemd_service", lambda: True)
    monkeypatch.setattr(mgr, "_wait_for_socket", lambda timeout=15.0: True)
    monkeypatch.setattr(mgr, "_reset_ipc_session", lambda: None)

    mgr._force_restart_mpv_for_hung_recovery()

    assert called == [True]


def test_note_playback_ipc_failure_schedules_hung_recovery(null_logger, monkeypatch):
    mgr = MPVManager(logger=null_logger, socketio=None, upload_folder="/tmp", mpv_socket="/tmp/mpv.sock")
    mgr.set_playback_session_active(True)
    scheduled: list[bool] = []
    monkeypatch.setattr(mgr, "_schedule_hung_recovery", lambda: scheduled.append(True))
    monkeypatch.setenv("DSIGN_MPV_PLAYBACK_HUNG_RESTART_AFTER", "3")

    for _ in range(3):
        mgr._note_playback_ipc_failure(MPVIPCClosedError("dead"))

    assert scheduled == [True]


def test_resume_slideshow_after_crash_restarts_playlist(null_logger, monkeypatch):
    svc = _make_recovery_service(null_logger)
    svc._playlist_manager.get_resume_start_index.return_value = 1
    svc._playlist_manager.play.return_value = True
    svc._resolve_playback_resume_context = MagicMock(return_value=(7, "schedule", 5))

    svc._resume_slideshow_after_crash()

    svc._playlist_manager.play.assert_called_once_with(
        7, start_index=1, source="schedule", rule_id=5
    )


def test_maybe_recover_playback_desync_skips_during_boot_grace(null_logger, monkeypatch):
    svc = _make_recovery_service(null_logger)
    svc._app_ready = Event()
    svc._app_ready.set()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager.in_boot_grace.return_value = True
    monkeypatch.setenv("DSIGN_PLAYBACK_DESYNC_COALESCE_SEC", "0")

    svc._maybe_recover_playback_desync()

    svc.play.assert_not_called()
    svc._playlist_manager._remote_playback_snapshot.assert_not_called()


def test_maybe_recover_playback_desync_clears_ghost_idle_without_resume(null_logger, monkeypatch):
    """Ghost playing + idle mpv must clear DB — never auto-resume (schedule reclaim loop)."""
    svc = _make_recovery_service(null_logger, db_source="schedule", db_playlist_id=6)
    svc._app_ready = Event()
    svc._app_ready.set()
    svc._resolve_playback_resume_context = MagicMock(return_value=(6, "schedule", 11))
    svc._playlist_manager._remote_playback_snapshot.return_value = {
        "db_status": "playing",
        "db_playlist_id": 6,
        "thread_alive": False,
        "mpv_idle": True,
        "idle_logo": False,
    }
    svc._playlist_manager._mpv_has_active_media.return_value = False
    svc._playlist_manager._mpv_showing_idle_logo.return_value = False
    monkeypatch.setenv("DSIGN_PLAYBACK_DESYNC_COALESCE_SEC", "0")

    svc._maybe_recover_playback_desync()

    svc.play.assert_not_called()
    svc._playlist_manager._persist_playback_status.assert_called_once_with(
        playlist_id=None,
        status="idle",
        source="idle",
        clear_rule=True,
    )
    assert svc._schedule_play_cooldown_until > 0


def test_maybe_recover_playback_desync_clears_manual_to_stopped(null_logger, monkeypatch):
    svc = _make_recovery_service(null_logger, db_source="manual", db_playlist_id=6)
    svc._app_ready = Event()
    svc._app_ready.set()
    svc._playlist_manager._remote_playback_snapshot.return_value = {
        "db_status": "playing",
        "db_playlist_id": 6,
        "thread_alive": False,
        "mpv_idle": True,
        "idle_logo": False,
    }
    svc._playlist_manager._mpv_has_active_media.return_value = False
    svc._playlist_manager._mpv_showing_idle_logo.return_value = False
    monkeypatch.setenv("DSIGN_PLAYBACK_DESYNC_COALESCE_SEC", "0")

    svc._maybe_recover_playback_desync()

    svc.play.assert_not_called()
    svc._playlist_manager._persist_playback_status.assert_called_once_with(
        playlist_id=6,
        status="stopped",
        source="manual",
        clear_rule=True,
    )


def test_maybe_recover_playback_desync_clears_when_idle_logo(null_logger, monkeypatch):
    """DB playing + logo on screen must clear status, not resume schedule play."""
    svc = _make_recovery_service(null_logger, db_source="schedule", db_playlist_id=6)
    svc._app_ready = Event()
    svc._app_ready.set()
    svc.socketio = MagicMock()
    svc._playlist_manager._remote_playback_snapshot.return_value = {
        "db_status": "playing",
        "db_playlist_id": 6,
        "thread_alive": False,
        "mpv_idle": False,
        "idle_logo": True,
    }
    svc._playlist_manager._mpv_has_active_media.return_value = False
    monkeypatch.setenv("DSIGN_PLAYBACK_DESYNC_COALESCE_SEC", "0")

    svc._maybe_recover_playback_desync()

    svc.play.assert_not_called()
    svc._playlist_manager._persist_playback_status.assert_called_once_with(
        playlist_id=None,
        status="idle",
        source="idle",
        clear_rule=True,
    )


def test_recover_after_mpv_restart_clears_stale_status_on_play_failure(null_logger):
    svc = _make_recovery_service(null_logger, db_source="schedule", db_playlist_id=7)
    svc._playlist_manager.get_resume_start_index_for_hung_recovery.return_value = 0
    svc._mpv_manager.wait_for_ipc_socket_at_startup.return_value = True
    svc._mpv_manager.initialize.return_value = True
    svc.play.return_value = False

    ok = svc.recover_after_mpv_systemd_restart()

    assert ok is False
    svc._playlist_manager._persist_playback_status.assert_called_once_with(
        playlist_id=None,
        status="idle",
        source="idle",
        clear_rule=True,
    )
