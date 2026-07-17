"""Orphan ytdl loadfile must not block Stop→Play behind _ipc_lock forever."""

from __future__ import annotations

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
    release.set()
    thr.join(timeout=2.0)


def test_play_exec_lock_serializes_bodies(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    order: list[str] = []
    hold = Event()
    started = Event()

    def _slow_impl(*_a, **_k):
        order.append("enter")
        started.set()
        hold.wait(timeout=3.0)
        order.append("leave")
        return True

    pm._play_impl = _slow_impl  # type: ignore[method-assign]
    pm._app = None

    def _run():
        # Bypass app_context by calling acquire + impl path used by play().
        assert pm._acquire_play_exec(playlist_id=1)
        try:
            pm._play_impl(1)
        finally:
            pm._release_play_exec()

    t1 = Thread(target=_run, daemon=True)
    t1.start()
    assert started.wait(timeout=2.0)

    # Second acquire must wait until first releases.
    assert pm._play_exec_lock.acquire(blocking=False) is False
    hold.set()
    t1.join(timeout=2.0)
    assert order == ["enter", "leave"]
    assert pm._acquire_play_exec(playlist_id=2)
    pm._release_play_exec()


def test_interrupt_blocked_ipc_resets_session(null_logger, tmp_path):
    from dsign.services.mpv_management import MPVManager

    mgr = MPVManager(null_logger, None, str(tmp_path))
    sess = MagicMock()
    mgr._ipc_session = sess
    mgr.interrupt_blocked_ipc()
    sess.reset.assert_called_once()
