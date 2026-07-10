"""Graceful shutdown tests (backlog H-SD)."""

from __future__ import annotations

from contextlib import nullcontext
from threading import Thread
from unittest.mock import MagicMock

import pytest

from dsign.services.playback_service import PlaybackService


def _make_shutdown_service(null_logger, *, join_timeout: float = 8.0) -> PlaybackService:
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = null_logger
    svc._shutdown_started = False
    svc._schedule_engine = MagicMock()
    svc._playlist_manager = MagicMock()
    svc._mpv_manager = MagicMock()
    svc._app = MagicMock()
    svc._log_info = lambda *args, **kwargs: None
    svc._log_warning = lambda *args, **kwargs: None
    svc._app_context = lambda: nullcontext()
    svc._shutdown_join_timeout_sec = lambda: join_timeout
    return svc


def test_graceful_shutdown_stops_schedule_playback_and_mpv(null_logger, monkeypatch):
    svc = _make_shutdown_service(null_logger)
    remove_mock = MagicMock()
    monkeypatch.setattr("dsign.extensions.db.session.remove", remove_mock)

    svc.graceful_shutdown(signal_num=15)

    svc._schedule_engine.stop.assert_called_once()
    svc._playlist_manager.stop.assert_called_once_with(
        show_idle_logo=True,
        update_status=True,
        source="shutdown",
        join_timeout=8.0,
    )
    svc._mpv_manager.shutdown.assert_called_once()
    remove_mock.assert_called_once()
    assert svc._shutdown_started is True


def test_graceful_shutdown_is_idempotent(null_logger, monkeypatch):
    svc = _make_shutdown_service(null_logger)
    monkeypatch.setattr("dsign.extensions.db.session.remove", MagicMock())

    svc.graceful_shutdown()
    svc.graceful_shutdown()

    svc._schedule_engine.stop.assert_called_once()
    svc._playlist_manager.stop.assert_called_once()


def test_stop_play_thread_joins_with_custom_timeout(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())

    def _worker():
        while not pm._stop_event.wait(timeout=0.05):
            pass

    thread = Thread(target=_worker, name="test-play", daemon=True)
    pm._play_thread = thread
    thread.start()

    pm._stop_play_thread(join_timeout=0.5)

    assert pm._play_thread is None
    assert not thread.is_alive()
