"""Unit tests for playback_network module (H-REF PR2 extract)."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_network import PlaybackNetworkHelper


def _helper(null_logger, tmp_path) -> PlaybackNetworkHelper:
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    return PlaybackNetworkHelper(pm)


def test_normalize_mpv_http_headers_single_referer(null_logger, tmp_path):
    net = _helper(null_logger, tmp_path)
    out = net._normalize_mpv_http_headers(
        {"referer": "https://vk.com/video/1", "Referrer": "https://vk.com/dup"},
        provider="vkvideo",
    )
    assert out.get("Referer") == "https://vk.com/video/1"
    assert "Referrer" not in out
    assert "User-Agent" in out


def test_normalize_mpv_http_headers_strips_disallowed(null_logger, tmp_path):
    net = _helper(null_logger, tmp_path)
    out = net._normalize_mpv_http_headers(
        {"Sec-Fetch-Mode": "cors", "User-Agent": "test"},
        provider="rutube",
    )
    assert "Sec-Fetch-Mode" not in out
    assert out.get("User-Agent") == "test"


def test_sanitize_headers_prefers_external_media_service(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    svc = MagicMock()
    svc.sanitize_mpv_http_headers.return_value = {"Referer": "https://svc/"}
    pm._external_media_service = svc
    net = PlaybackNetworkHelper(pm)
    out = net._sanitize_headers_for_mpv({}, page_url="https://example.com")
    svc.sanitize_mpv_http_headers.assert_called_once()
    assert out["Referer"] == "https://svc/"


def test_escape_mpv_key_value_list_token(null_logger, tmp_path):
    net = _helper(null_logger, tmp_path)
    assert net._escape_mpv_key_value_list_token("a=b,c") == r"a\=b\,c"
