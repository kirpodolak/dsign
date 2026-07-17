"""Stop/Play must not hold play handoff across mpv halt or control_lock (live: play_lock_timeout)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from dsign.services.playback_play import PlaybackPlayRunner
from dsign.services.playlist_management import PlaylistManager


def test_stop_play_thread_halts_only_outside_caller_lock(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    held_during_halt = []

    def _prepare(*, lock_wait=1.0):
        held_during_halt.append(pm._play_start_lock.locked())

    pm._prepare_mpv_for_new_play = _prepare  # type: ignore[method-assign]
    pm._play_thread = None
    pm._orphan_play_threads = []

    # Simulate caller holding handoff (the old buggy Stop/Play path).
    assert pm._play_start_lock.acquire(blocking=False)
    try:
        # New contract: callers must not halt under handoff; halt_mpv still runs
        # prepare, so this documents the danger if misused.
        pm._stop_play_thread(halt_mpv=True)
        assert held_during_halt == [True]
    finally:
        pm._play_start_lock.release()


def test_play_runner_stop_and_prepare_before_handoff(null_logger, tmp_path):
    """Order regression: join/prepare must precede handoff acquire."""
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    order: list[str] = []

    pm.mark_play_starting = MagicMock()
    pm._stop_play_thread = lambda **k: order.append("stop")  # type: ignore[method-assign]
    pm._prepare_mpv_for_new_play = lambda **k: order.append("prepare")  # type: ignore[method-assign]
    pm._cancel_content_cache_prefetches = lambda: order.append("cancel")  # type: ignore[method-assign]
    pm._prune_media_backoff = lambda: order.append("prune")  # type: ignore[method-assign]
    pm._acquire_play_handoff = lambda **k: order.append("acquire") or False  # type: ignore[method-assign]
    pm._release_play_handoff = MagicMock()

    assert PlaybackPlayRunner(pm).run(1) is False
    assert order == ["stop", "prepare", "cancel", "prune", "acquire"]


def test_stop_impl_does_not_take_play_handoff(null_logger, tmp_path):
    """Stop must use control_lock only — taking handoff deadlocked Play workers."""
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._app = None
    order: list[str] = []
    pm._signal_play_threads_stop = lambda: order.append("signal")  # type: ignore[method-assign]
    pm._join_play_threads = lambda **k: order.append("join")  # type: ignore[method-assign]
    pm._clear_play_thread_state = lambda **k: order.append("clear")  # type: ignore[method-assign]
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._halt_mpv_playback = MagicMock(return_value=True)
    pm._mpv_content_still_on_air = MagicMock(return_value=False)
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm._persist_playback_status = MagicMock()
    pm._acquire_play_handoff = MagicMock(return_value=True)  # type: ignore[method-assign]
    pm._release_play_handoff = MagicMock()  # type: ignore[method-assign]

    row = MagicMock()
    row.playlist_id = 6
    pm.db_session.query.return_value.get.return_value = row

    assert pm._stop_impl(show_idle_logo=True, update_status=True, source="manual") is True
    assert order[:3] == ["signal", "join", "clear"]
    pm._acquire_play_handoff.assert_not_called()
    pm._release_play_handoff.assert_not_called()


def test_stop_progresses_while_play_handoff_held(null_logger, tmp_path):
    """Live failure mode: handoff stuck must not block Stop status/halt."""
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._app = None
    pm._signal_play_threads_stop = MagicMock()  # type: ignore[method-assign]
    pm._join_play_threads = MagicMock()  # type: ignore[method-assign]
    pm._clear_play_thread_state = MagicMock()  # type: ignore[method-assign]
    pm._cancel_content_cache_prefetches = MagicMock()
    pm._logo_manager.ensure_mpv_video_output = MagicMock()
    pm._halt_mpv_playback = MagicMock(return_value=True)
    pm._mpv_content_still_on_air = MagicMock(return_value=False)
    pm._logo_manager.display_idle_logo = MagicMock(return_value=True)
    pm._persist_playback_status = MagicMock()
    row = MagicMock()
    row.playlist_id = 6
    pm.db_session.query.return_value.get.return_value = row

    assert pm._play_start_lock.acquire(blocking=False)
    try:
        done = []

        def _stop() -> None:
            done.append(pm._stop_impl(show_idle_logo=True, update_status=True, source="manual"))

        thr = threading.Thread(target=_stop, daemon=True)
        thr.start()
        thr.join(timeout=2.0)
        assert not thr.is_alive()
        assert done == [True]
        pm._persist_playback_status.assert_called()
    finally:
        pm._play_start_lock.release()


def test_commit_play_runs_on_abort_outside_control_lock(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    held = []

    def _abort() -> None:
        held.append(pm._control_lock.locked())

    seq = pm._begin_play_seq()
    pm._bump_play_seq()  # invalidate before commit
    assert pm._commit_play(seq, start_thread=lambda: None, persist=lambda: None, on_abort=_abort) is False
    assert held == [False]
