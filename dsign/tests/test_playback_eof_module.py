"""Unit tests for playback_eof module (H-REF PR1 extract)."""

from __future__ import annotations

from dsign.services.playback_eof import (
    is_external_stream_provider,
    network_stream_near_eof,
)


def test_is_external_stream_provider_module():
    assert is_external_stream_provider(provider="rutube") is True
    assert is_external_stream_provider(stream_url="ytdl://vk/1") is True
    assert is_external_stream_provider(stream_url="https://example.com/v.mp4") is False


def test_network_stream_near_eof_module(monkeypatch):
    monkeypatch.setenv("DSIGN_MPV_NETWORK_EOF_ADVANCE_SEC", "8")
    assert network_stream_near_eof(time_pos=95.0, duration=100.0) is True
    assert network_stream_near_eof(time_pos=10.0, duration=100.0) is False
