"""ContentCache prefetch pool (backlog H-PREF)."""

from __future__ import annotations

from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest

from dsign.services.content_cache_prefetch import prefetch_workers
from dsign.services.content_cache import ContentCache


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def test_prefetch_workers_default():
    assert prefetch_workers() == 1


def test_prefetch_workers_env(monkeypatch):
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_PREFETCH_WORKERS", "3")
    assert prefetch_workers() == 3


def test_prefetch_executor_max_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_ENABLED", "1")
    cache = ContentCache(str(tmp_path), _NullLogger())
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_PREFETCH_WORKERS", "2")
    executor = cache._get_prefetch_executor()
    assert executor._max_workers == 2
    cache.shutdown_prefetch_pool(wait=False)


def test_cancel_prefetches_stops_download(tmp_path):
    cache = ContentCache(str(tmp_path), _NullLogger())
    proc = MagicMock()
    proc.poll.return_value = None
    proc.terminate = MagicMock()

    with cache._prefetch_lock:
        cache._active_prefetch["ext-stop1"] = MagicMock(spec=Future)
        cache._active_prefetch["ext-stop1"].done.return_value = False
        cache._active_download_procs["ext-stop1"] = proc

    removed = cache.cancel_prefetches()

    assert removed == 1
    proc.terminate.assert_called_once()
    assert cache._is_prefetch_cancelled("ext-stop1")


def test_play_impl_cancels_content_cache_prefetches(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    cache = MagicMock()
    cache.cancel_prefetches.return_value = 1
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm.set_content_cache(cache)
    pm._stop_play_thread = MagicMock()
    pm._prune_media_backoff = MagicMock()
    pm._set_playback_active_marker = MagicMock()
    pm._logo_manager = MagicMock()
    pm.db_session.query.return_value.get.return_value = None

    with pytest.raises(RuntimeError, match="Failed to start playback"):
        pm._play_impl(999)

    cache.cancel_prefetches.assert_called_once()
