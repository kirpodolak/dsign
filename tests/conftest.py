"""Shared pytest fixtures for dsign unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from fake_mpv_ipc import FakeMpvIpcServer, default_echo_handler


@pytest.fixture
def fake_mpv_socket(tmp_path: Path):
    """Running fake mpv IPC server; yields (socket_path, server)."""
    sock_path = str(tmp_path / "mpv.sock")
    server = FakeMpvIpcServer(sock_path)
    server.set_handler(default_echo_handler)
    server.start()
    try:
        yield sock_path, server
    finally:
        server.stop()


@pytest.fixture
def null_logger():
    class _Logger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    return _Logger()
