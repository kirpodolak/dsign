"""Orphan ytdl loadfile must not block Stop→Play behind _ipc_lock forever."""

from __future__ import annotations

import time
from threading import Event, Thread
from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_join_timeout_interrupts_ipc_and_halts(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    release = Event()
    interrupted = []

    def _worker():
        release.wait(timeout=5.0)

    thr = Thread(target=_worker, name="stuck-ytdl", daemon=True)
    pm._play_thread = thr
    thr.start()

    pm._mpv_manager.interrupt_blocked_ipc = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda: interrupted.append("ipc")
    )
    pm._halt_mpv_playback = MagicMock(return_value=True)  # type: ignore[method-assign]

    pm._join_play_threads(join_timeout=0.15, interrupt_ipc_on_timeout=True)

    assert interrupted == ["ipc"]
    pm._halt_mpv_playback.assert_called_once()
    assert len(pm._orphan_play_threads) == 1
    assert thr.is_alive()
    assert pm._post_orphan_ipc_until > time.monotonic()
    assert pm._loadfile_ipc_lock_wait_sec() == 5.0

    release.set()
    thr.join(timeout=2.0)


def test_stop_play_thread_interrupts_on_orphan(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    release = Event()

    def _worker():
        release.wait(timeout=5.0)

    thr = Thread(target=_worker, name="stuck-ytdl", daemon=True)
    pm._play_thread = thr
    thr.start()

    pm._mpv_manager.interrupt_blocked_ipc = MagicMock()  # type: ignore[method-assign]
    pm._halt_mpv_playback = MagicMock(return_value=True)  # type: ignore[method-assign]
    pm._prepare_mpv_for_new_play = MagicMock()  # type: ignore[method-assign]

    pm._stop_play_thread(join_timeout=0.15, halt_mpv=False)

    pm._mpv_manager.interrupt_blocked_ipc.assert_called()
    # Sticky stop kept while orphan alive — new Play clears after handoff.
    assert pm._stop_event.is_set()
    release.set()
    thr.join(timeout=2.0)


def test_interrupt_blocked_ipc_resets_session(null_logger, tmp_path):
    from dsign.services.mpv_management import MPVManager

    mgr = MPVManager(null_logger, None, str(tmp_path))
    sess = MagicMock()
    mgr._ipc_session = sess
    mgr.interrupt_blocked_ipc()
    sess.reset.assert_called_once()


def test_play_does_not_hold_exec_lock_across_body():
    """Regression: full-body exec lock caused play_async_false after Stop→Play."""
    from dsign.services import playlist_management as pm_mod
    import inspect

    src = inspect.getsource(pm_mod.PlaylistManager.play)
    assert "_acquire_play_exec" not in src
    assert "_play_exec_lock" not in src
