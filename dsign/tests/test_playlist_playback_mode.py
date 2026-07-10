"""Playlist playback mode routing — local / mixed / network (pytest Tier 2 / T-MIX)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dsign.services.playlist_management import PlaylistManager


def _pm(null_logger, tmp_path) -> PlaylistManager:
    return PlaylistManager(
        null_logger,
        None,
        str(tmp_path),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )


def test_playlist_playback_mode_empty_manual(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    assert pm._playlist_playback_mode([]) == "manual"


def test_playlist_playback_mode_local_single(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    items = [{"path": str(tmp_path / "a.mp4"), "is_video": True}]
    assert pm._playlist_playback_mode(items) == "local_single"


def test_playlist_playback_mode_local_playlist(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    items = [
        {"path": str(tmp_path / "a.mp4"), "is_video": True},
        {"path": str(tmp_path / "b.mp4"), "is_video": True},
    ]
    assert pm._playlist_playback_mode(items) == "local_playlist"


def test_playlist_playback_mode_mixed_manual(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    items = [
        {"path": str(tmp_path / "a.mp4"), "is_video": True},
        {"path": str(tmp_path / "slide.jpg"), "is_video": False},
    ]
    assert pm._playlist_playback_mode(items) == "manual"


def test_playlist_playback_mode_network_manual(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    items = [
        {"path": str(tmp_path / "a.mp4"), "is_video": True},
        {"path": "ytdl://vkvideo/123", "is_video": True},
    ]
    assert pm._playlist_playback_mode(items) == "manual"


def test_classify_local_media_suffix(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    assert pm._classify_local_media_suffix(".mp4") == (True, False)
    assert pm._classify_local_media_suffix(".MP3") == (False, True)
    assert pm._classify_local_media_suffix(".jpg") == (False, False)


def test_resolve_playlist_item_path_local_file(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    resolved = pm._resolve_playlist_item_path("clip.mp4")
    assert resolved is not None
    assert resolved["path"] == str(media)
    assert resolved["is_video"] is True


def test_resolve_playlist_item_path_uses_content_cache(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    cache = MagicMock()
    cache.build_playback_dict.return_value = {
        "key": "ext-cache1",
        "path": "/var/cache/ext-cache1.mp4",
        "is_video": True,
        "from_content_cache": True,
    }
    pm.set_content_cache(cache)
    ext = MagicMock()
    ext.url = "https://rutube.ru/video/abc"
    ext.provider = "rutube"
    ext.resolved_url = "https://cdn.example/stream"
    svc = MagicMock()
    svc.get_by_key.return_value = ext
    pm._external_media_service = svc

    resolved = pm._resolve_playlist_item_path("ext-cache1")
    cache.build_playback_dict.assert_called_once()
    assert resolved["from_content_cache"] is True
    assert resolved["path"] == "/var/cache/ext-cache1.mp4"


def test_resolve_playlist_item_path_falls_back_to_fresh_url(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    cache = MagicMock()
    cache.build_playback_dict.return_value = None
    pm.set_content_cache(cache)
    row = MagicMock()
    row.url = "https://vk.com/video123"
    row.resolved_url = "https://cdn.vk/old"
    row.provider = "vkvideo"
    svc = MagicMock()
    svc.get_by_key.return_value = row
    svc.ensure_fresh_playback.return_value = {
        "url": "https://cdn.vk/fresh",
        "http_headers": {"User-Agent": "mpv"},
    }
    pm._external_media_service = svc

    resolved = pm._resolve_playlist_item_path("ext-fresh1")
    svc.ensure_fresh_playback.assert_called_once_with(row, max_age_sec=0)
    assert resolved["path"] == "https://cdn.vk/fresh"
    assert resolved["http_headers"] == {"User-Agent": "mpv"}


def test_refresh_item_playback_path_updates_external_url(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    item = {
        "key": "ext-loop1",
        "path": "https://cdn.example/old",
        "http_headers": {},
    }
    pm._resolve_playlist_item_path = MagicMock(
        return_value={
            "path": "https://cdn.example/new",
            "http_headers": {"Referer": "https://vk.com"},
            "page_url": "https://vk.com/video",
            "provider": "vkvideo",
        }
    )
    assert pm._refresh_item_playback_path(item) is True
    assert item["path"] == "https://cdn.example/new"
    assert item["http_headers"] == {"Referer": "https://vk.com"}


def test_refresh_item_playback_path_skips_local(null_logger, tmp_path):
    pm = _pm(null_logger, tmp_path)
    item = {"key": "local.mp4", "path": str(tmp_path / "local.mp4")}
    pm._resolve_playlist_item_path = MagicMock()
    assert pm._refresh_item_playback_path(item) is True
    pm._resolve_playlist_item_path.assert_not_called()
