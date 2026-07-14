"""Unit tests for PlaylistManager EOF detection paths (backlog T-EOF)."""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from dsign.services.playlist_management import PlaylistManager


class FakeClock:
  def __init__(self, start: float = 10_000.0) -> None:
      self.t = start

  def monotonic(self) -> float:
      return self.t

  def advance(self, sec: float) -> None:
      self.t += sec


@pytest.fixture
def eof_setup(null_logger, tmp_path):
    mpv = MagicMock()
    mpv._playback_eof_events_enabled = False
    mpv.drain_playback_events = MagicMock(return_value=0)
    mpv.set_playback_network_active = MagicMock()
    logo = MagicMock()
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), mpv, logo)
    pm._active_playlist_id = 1
    pm._stop_event = Event()
    clock = FakeClock()
    return pm, mpv, clock


def _bind_clock(pm: PlaylistManager, clock: FakeClock, monkeypatch) -> None:
    import time

    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    def _wait(timeout: float = 0) -> bool:
        clock.advance(max(0.01, float(timeout)))
        return False

    monkeypatch.setattr(pm._stop_event, "wait", _wait)


def test_is_external_stream_provider_detects_vk_and_rutube(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    assert pm._is_external_stream_provider(provider="vkvideo") is True
    assert pm._is_external_stream_provider(stream_url="ytdl://vkvideo/123") is True
    assert pm._is_external_stream_provider(stream_url="https://rutube.ru/video/abc") is True
    assert pm._is_external_stream_provider(stream_url="https://example.com/v.mp4") is False


def test_eof_end_file_event(eof_setup, monkeypatch):
    """Path 1: mpv end-file event with reason=eof."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")

    def _enable() -> None:
        mpv._playback_eof_events_enabled = True

    mpv.enable_playback_eof_events.side_effect = _enable
    mpv.wait_playback_event.return_value = {"event": "end-file", "reason": "eof"}

    with patch.object(pm, "_mpv_get_light", return_value=None):
        clock.advance(3.5)
        ok = pm._wait_mpv_video_end(
            1,
            is_network=True,
            stream_ready=True,
            provider="vkvideo",
            stream_url="ytdl://vk/1",
        )

    assert ok is True


def test_eof_network_idle(eof_setup, monkeypatch):
    """Path 2: network stream returns to idle after stream_ready."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")
    monkeypatch.setenv("DSIGN_MPV_NETWORK_EOF_POLL_SEC", "1")

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "idle-active":
            return True
        if prop == "time-pos":
            return 12.0
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(3.5)
        ok = pm._wait_mpv_video_end(1, is_network=True, stream_ready=True)

    assert ok is True


def test_eof_local_idle(eof_setup, monkeypatch):
    """Path 3: local file idle-active after grace."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "idle-active":
            return True
        if prop == "time-pos":
            return 0.0
        if prop == "duration":
            return 30.0
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(0.5)
        ok = pm._wait_mpv_video_end(1, is_network=False, stream_ready=False)

    assert ok is True


def test_eof_local_stagnation(eof_setup, monkeypatch):
    """Path 4: frozen time-pos on local file."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_PLAYBACK_STAGNATION_SEC", "20")

    calls = {"n": 0}

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "time-pos":
            calls["n"] += 1
            return 15.0
        if prop == "idle-active":
            return False
        if prop == "duration":
            return 120.0
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(0.5)
        clock.advance(21.0)
        ok = pm._wait_mpv_video_end(1, is_network=False, stream_ready=False)

    assert ok is True
    assert calls["n"] >= 2


def test_eof_network_near_eof_stagnation(eof_setup, monkeypatch):
    """Path 5: VK/HLS near duration with frozen time-pos."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")
    monkeypatch.setenv("DSIGN_MPV_NETWORK_NEAR_EOF_STAGNATION_SEC", "5")
    monkeypatch.setenv("DSIGN_MPV_NETWORK_EOF_ADVANCE_SEC", "8")

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "time-pos":
            return 95.0
        if prop == "duration":
            return 100.0
        if prop == "idle-active":
            return False
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(3.5)
        clock.advance(6.0)
        ok = pm._wait_mpv_video_end(
            1,
            is_network=True,
            stream_ready=True,
            provider="vkvideo",
            stream_url="ytdl://vk/1",
        )

    assert ok is True


def test_eof_network_duration_reached(eof_setup, monkeypatch):
    """Path 6: eof-capable network stream reaches duration threshold."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")
    monkeypatch.setenv("DSIGN_MPV_NETWORK_EOF_ADVANCE_SEC", "8")

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "time-pos":
            return 93.0
        if prop == "duration":
            return 100.0
        if prop == "idle-active":
            return False
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(3.5)
        ok = pm._wait_mpv_video_end(
            1,
            is_network=True,
            stream_ready=True,
            provider="rutube",
            stream_url="https://rutube.ru/video/x",
        )

    assert ok is True


def test_eof_reached_property_paused_network(eof_setup, monkeypatch):
    """Rutube/HLS: eof-reached=true while pause=true must advance (Pi stuck-at-EOF)."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")
    monkeypatch.setenv("DSIGN_MPV_NETWORK_EOF_POLL_SEC", "1")

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "time-pos":
            return 3636.67
        if prop == "duration":
            return 3636.67
        if prop == "idle-active":
            return False
        if prop == "eof-reached":
            return True
        if prop == "pause":
            return True
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(3.5)
        ok = pm._wait_mpv_video_end(
            1,
            is_network=True,
            stream_ready=True,
            provider="rutube",
            stream_url="ytdl://https://rutube.ru/video/d2465847a301b47b84ad734a0a0aaca0/",
            media_key="ext-4",
        )

    assert ok is True


def test_eof_reached_property_unpaused(eof_setup, monkeypatch):
    """eof-reached=true without pause also finishes the item after grace."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "time-pos":
            return 100.0
        if prop == "idle-active":
            return False
        if prop == "eof-reached":
            return True
        if prop == "pause":
            return False
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(3.5)
        ok = pm._wait_mpv_video_end(
            1,
            is_network=True,
            stream_ready=True,
            provider="rutube",
            stream_url="https://rutube.ru/video/x",
        )

    assert ok is True


def test_eof_reached_unavailable_still_uses_idle(eof_setup, monkeypatch):
    """When eof-reached is unavailable, previous idle path still works."""
    pm, mpv, clock = eof_setup
    _bind_clock(pm, clock, monkeypatch)
    monkeypatch.setenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC", "3")
    monkeypatch.setenv("DSIGN_MPV_NETWORK_EOF_POLL_SEC", "1")

    def _get_light(prop: str, *, timeout: float = 8.0):
        if prop == "idle-active":
            return True
        if prop == "time-pos":
            return 12.0
        if prop == "eof-reached":
            return None
        return None

    with patch.object(pm, "_mpv_get_light", side_effect=_get_light):
        clock.advance(3.5)
        ok = pm._wait_mpv_video_end(1, is_network=True, stream_ready=True)

    assert ok is True
