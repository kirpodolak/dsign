"""ContentCache download retry backoff (backlog H-CACHE)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dsign.services.content_cache import ContentCache
from dsign.services.content_cache_retry import (
    DEFAULT_CONTENT_CACHE_DOWNLOAD_ATTEMPTS,
    DEFAULT_CONTENT_CACHE_RETRY_BASE_SEC,
    DEFAULT_CONTENT_CACHE_RETRY_MAX_SEC,
    download_max_attempts,
    download_retry_delay_sec,
)


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def test_download_max_attempts_default():
    assert download_max_attempts() == DEFAULT_CONTENT_CACHE_DOWNLOAD_ATTEMPTS


def test_download_max_attempts_env(monkeypatch):
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_DOWNLOAD_ATTEMPTS", "4")
    assert download_max_attempts() == 4


def test_download_retry_delay_sec_exponential_cap(monkeypatch):
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_RETRY_BASE_SEC", "2")
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_RETRY_MAX_SEC", "10")
    assert download_retry_delay_sec(1) == 2.0
    assert download_retry_delay_sec(2) == 4.0
    assert download_retry_delay_sec(3) == 8.0
    assert download_retry_delay_sec(4) == 10.0


def test_download_retry_delay_defaults():
    assert download_retry_delay_sec(1) == DEFAULT_CONTENT_CACHE_RETRY_BASE_SEC
    assert download_retry_delay_sec(99) <= DEFAULT_CONTENT_CACHE_RETRY_MAX_SEC


def test_download_retries_until_success(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_DOWNLOAD_ATTEMPTS", "3")
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_RETRY_BASE_SEC", "0")
    calls = {"n": 0}

    def _once(*_args, **_kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(cache, "_download_once", _once)
    monkeypatch.setattr(cache, "_is_prefetch_cancelled", lambda _k: False)
    monkeypatch.setattr(cache, "is_ready", lambda _k: False)
    monkeypatch.setattr(cache, "_ytdl_path", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("os.path.isfile", lambda _p: True)

    assert cache._download("ext-retry1", "https://example.com/v", "rutube") is True
    assert calls["n"] == 2


def test_download_exhausts_retries(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_DOWNLOAD_ATTEMPTS", "2")
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_RETRY_BASE_SEC", "0")
    monkeypatch.setattr(cache, "_download_once", lambda *_a, **_k: False)
    monkeypatch.setattr(cache, "_is_prefetch_cancelled", lambda _k: False)
    monkeypatch.setattr(cache, "is_ready", lambda _k: False)
    monkeypatch.setattr(cache, "_ytdl_path", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("os.path.isfile", lambda _p: True)

    assert cache._download("ext-retry2", "https://example.com/v", "rutube") is False


def test_wait_download_retry_aborts_on_cancel(tmp_path):
    cache = ContentCache(str(tmp_path), _NullLogger())
    cache._cancelled_prefetch.add("ext-cancel1")
    assert cache._wait_download_retry("ext-cancel1", 1.0) is False
