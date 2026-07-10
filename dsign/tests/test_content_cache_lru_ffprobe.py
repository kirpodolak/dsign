"""ContentCache LRU eviction and ffprobe gates (pytest Tier 2 / T-CACHE)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dsign.services.content_cache import ContentCache


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def _touch_mp4(path: Path, *, size: int = 70_000, mtime: float | None = None) -> None:
    path.write_bytes(b"\x00" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_safe_key_rejects_invalid(tmp_path):
    cache = ContentCache(str(tmp_path), _NullLogger())
    assert cache._safe_key("") is None
    assert cache._safe_key("video.mp4") is None
    assert cache._safe_key("ext-bad key") is None
    assert cache._safe_key("ext-abc123") == "ext-abc123"


def test_ffprobe_ok_accepts_video(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")

    class _Proc:
        returncode = 0
        stdout = "video\n"

    monkeypatch.setattr(
        "dsign.services.content_cache.subprocess.run",
        lambda *a, **k: _Proc(),
    )
    assert cache._ffprobe_ok(path) is True


def test_ffprobe_ok_rejects_non_video(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")

    class _Proc:
        returncode = 0
        stdout = "audio\n"

    monkeypatch.setattr(
        "dsign.services.content_cache.subprocess.run",
        lambda *a, **k: _Proc(),
    )
    assert cache._ffprobe_ok(path) is False


def test_is_ready_rejects_small_file(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    key = "ext-small"
    path = cache._media_path(key)
    _touch_mp4(path, size=1000)
    monkeypatch.setattr(cache, "_ffprobe_ok", lambda _p: True)
    assert cache.is_ready(key) is False


def test_is_ready_accepts_valid_cached_file(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    key = "ext-ready1"
    path = cache._media_path(key)
    _touch_mp4(path, size=80_000)
    monkeypatch.setattr(cache, "_ffprobe_ok", lambda _p: True)
    assert cache.is_ready(key) is True
    assert cache.get_local_path(key) == path


def test_enforce_size_limit_evicts_oldest_first(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    monkeypatch.setattr(cache, "_max_bytes", lambda: 150_000)
    now = time.time()
    old_path = cache._media_path("ext-old")
    mid_path = cache._media_path("ext-mid")
    new_path = cache._media_path("ext-new")
    _touch_mp4(old_path, size=60_000, mtime=now - 300)
    _touch_mp4(mid_path, size=60_000, mtime=now - 200)
    _touch_mp4(new_path, size=60_000, mtime=now - 100)
    for key in ("ext-old", "ext-mid", "ext-new"):
        cache._write_meta(key, {"media_key": key})

    cache._enforce_size_limit()

    assert not old_path.exists()
    assert mid_path.exists()
    assert new_path.exists()
    assert not cache._meta_path("ext-old").exists()


def test_should_use_cache_when_offline(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    key = "ext-off1"
    monkeypatch.setattr(cache, "enabled", lambda: True)
    monkeypatch.setattr(cache, "is_ready", lambda _k: True)
    monkeypatch.setattr(cache, "has_internet", lambda **_: False)
    assert cache.should_use_cache_for_playback(key) is True


def test_should_not_use_cache_when_online_and_play_when_ready_off(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    key = "ext-on1"
    monkeypatch.setattr(cache, "enabled", lambda: True)
    monkeypatch.setattr(cache, "is_ready", lambda _k: True)
    monkeypatch.setattr(cache, "has_internet", lambda **_: True)
    monkeypatch.setenv("DSIGN_CONTENT_CACHE_PLAY_WHEN_READY", "0")
    assert cache.play_when_ready() is False
    assert cache.should_use_cache_for_playback(key) is False


def test_has_internet_caches_probe(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    calls = {"n": 0}

    def _probe(*_a, **_k):
        calls["n"] += 1
        return True

    monkeypatch.setattr(cache, "_internet_cache", {"ok": None, "ts": 0.0})
    monkeypatch.setattr(
        "dsign.services.content_cache.socket.create_connection",
        lambda *a, **k: (_probe(), MagicMock())[1],
    )
    assert cache.has_internet() is True
    assert cache.has_internet() is True
    assert calls["n"] == 1
    assert cache.has_internet(force=True) is True
    assert calls["n"] == 2


def test_build_playback_dict_from_cache(tmp_path, monkeypatch):
    cache = ContentCache(str(tmp_path), _NullLogger())
    key = "ext-play1"
    path = cache._media_path(key)
    _touch_mp4(path, size=80_000)
    monkeypatch.setattr(cache, "_ffprobe_ok", lambda _p: True)
    monkeypatch.setattr(cache, "has_internet", lambda **_: False)
    out = cache.build_playback_dict(key, page_url="https://rutube.ru/v/1", provider="rutube")
    assert out is not None
    assert out["path"] == str(path)
    assert out["from_content_cache"] is True
    assert out["provider"] == "rutube"
