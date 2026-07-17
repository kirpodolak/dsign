"""Play handoff lock must not span loadfile/ytdl (Stop→Play lock timeout)."""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock

from dsign.services.playlist_management import PlaylistManager


def test_acquire_play_handoff_releases_before_loadfile_path(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    assert pm._acquire_play_handoff(playlist_id=1) is True
    assert pm._play_start_lock.locked()
    pm._release_play_handoff()
    assert not pm._play_start_lock.locked()


def test_play_does_not_acquire_lock_around_impl():
    """Regression: outer play() must not hold _play_start_lock across loadfile."""
    src = inspect.getsource(PlaylistManager.play)
    assert "_play_start_lock.acquire" not in src
    assert "_play_impl" in src
    assert "_acquire_play_handoff" in inspect.getsource(
        __import__(
            "dsign.services.playback_play", fromlist=["PlaybackPlayRunner"]
        ).PlaybackPlayRunner.run
    )


def test_handoff_retries_after_busy_lock(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._play_lock_timeout_sec = MagicMock(return_value=0.05)  # type: ignore[method-assign]
    pm._halt_mpv_playback = MagicMock(return_value=True)  # type: ignore[method-assign]

    assert pm._play_start_lock.acquire(blocking=False)
    result: list[bool] = []

    def _try() -> None:
        result.append(pm._acquire_play_handoff(playlist_id=6))

    thr = threading.Thread(target=_try, daemon=True)
    thr.start()
    # First attempt times out at 0.05s; release during the retry wait.
    time.sleep(0.08)
    pm._play_start_lock.release()
    thr.join(timeout=2.0)
    assert result == [True]
    pm._release_play_handoff()
    pm._halt_mpv_playback.assert_called()
