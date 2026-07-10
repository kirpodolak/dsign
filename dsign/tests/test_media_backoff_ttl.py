"""Media backoff TTL cleanup (backlog H-MEM)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dsign.services.media_backoff import (
    DEFAULT_MEDIA_BACKOFF_TTL_SEC,
    entry_last_touch_monotonic,
    media_backoff_ttl_sec,
    prune_stale_media_backoff,
)
from dsign.services.playlist_management import PlaylistManager


def test_media_backoff_ttl_sec_default():
    assert media_backoff_ttl_sec() == DEFAULT_MEDIA_BACKOFF_TTL_SEC


def test_media_backoff_ttl_sec_env(monkeypatch):
    monkeypatch.setenv("DSIGN_MEDIA_BACKOFF_TTL_SEC", "120")
    assert media_backoff_ttl_sec() == 120.0


def test_media_backoff_ttl_sec_clamped(monkeypatch):
    monkeypatch.setenv("DSIGN_MEDIA_BACKOFF_TTL_SEC", "10")
    assert media_backoff_ttl_sec() == 60.0
    monkeypatch.setenv("DSIGN_MEDIA_BACKOFF_TTL_SEC", "999999")
    assert media_backoff_ttl_sec() == 86400.0


def test_entry_last_touch_prefers_last_touch():
    entry = {"last_touch_monotonic": 100.0, "next_try_monotonic": 50.0}
    assert entry_last_touch_monotonic(entry) == 100.0


def test_prune_stale_media_backoff_removes_old_entries():
    backoff = {
        "fresh": {"last_touch_monotonic": 1600.0, "failures": 1},
        "stale": {"last_touch_monotonic": 100.0, "failures": 2},
        "corrupt": {"failures": 1},
    }
    removed = prune_stale_media_backoff(backoff, now=2000.0, ttl_sec=500.0)
    assert removed == 2
    assert "fresh" in backoff
    assert "stale" not in backoff
    assert "corrupt" not in backoff


def test_prune_stale_media_backoff_keeps_recent():
    backoff = {"a": {"next_try_monotonic": 1990.0, "failures": 1}}
    removed = prune_stale_media_backoff(backoff, now=2000.0, ttl_sec=500.0)
    assert removed == 0
    assert backoff == {"a": {"next_try_monotonic": 1990.0, "failures": 1}}


def test_register_media_failure_sets_last_touch_and_prunes(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._media_backoff = {
        "old": {"last_touch_monotonic": 10.0, "failures": 1},
    }

    pm._register_media_failure("new-key", reason="test")

    assert "new-key" in pm._media_backoff
    assert pm._media_backoff["new-key"]["last_touch_monotonic"] > 0
    assert "old" not in pm._media_backoff


def test_play_impl_prunes_stale_backoff(null_logger, tmp_path, monkeypatch):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._media_backoff = {
        "stale": {"last_touch_monotonic": 1.0, "failures": 1},
    }
    pm._stop_play_thread = MagicMock()
    pm._set_playback_active_marker = MagicMock()
    pm._logo_manager = MagicMock()
    pm.db_session.query.return_value.get.return_value = None

    with pytest.raises(RuntimeError, match="Failed to start playback"):
        pm._play_impl(999)

    assert "stale" not in pm._media_backoff
