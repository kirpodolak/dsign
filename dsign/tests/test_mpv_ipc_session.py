"""Unit tests for MpvJsonIpcSession (backlog T-IPC)."""

from __future__ import annotations

import json
import queue
import threading
import time

import pytest

from dsign.services.mpv_ipc_session import (
    MPVIPCClosedError,
    MPVIPCTimeoutError,
    MpvJsonIpcSession,
)

from fake_mpv_ipc import FakeMpvIpcServer, default_echo_handler


def test_command_success(fake_mpv_socket, null_logger):
    sock_path, _server = fake_mpv_socket
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        resp = sess.command({"command": ["get_property", "pause"]}, timeout=2.0, request_id=42)
        assert resp["error"] == "success"
        assert resp["request_id"] == 42
    finally:
        sess.close()


def test_commands_batch(fake_mpv_socket, null_logger):
    sock_path, server = fake_mpv_socket
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        items = [
            (1, {"command": ["get_property", "time-pos"]}),
            (2, {"command": ["get_property", "duration"]}),
        ]
        results = sess.commands_batch(items, timeout=2.0)
        assert len(results) == 2
        assert results[0]["request_id"] == 1
        assert results[1]["request_id"] == 2
        assert len(server.received) == 2
    finally:
        sess.close()


def test_commands_batch_empty(fake_mpv_socket, null_logger):
    sock_path, _ = fake_mpv_socket
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        assert sess.commands_batch([], timeout=1.0) == []
    finally:
        sess.close()


def test_event_subscription_wait_and_drain(fake_mpv_socket, null_logger):
    sock_path, server = fake_mpv_socket
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        sess.subscribe_event("end-file")
        # subscribe_event alone does not open the socket; connect before push_event.
        sess.command({"command": ["get_property", "pause"]}, timeout=2.0, request_id=0)
        server.push_event("end-file", {"reason": "eof"})
        ev = sess.wait_event("end-file", timeout=1.0)
        assert ev is not None
        assert ev.get("event") == "end-file"
        assert ev.get("reason") == "eof"

        server.push_event("end-file")
        deadline = time.monotonic() + 1.0
        drained = 0
        while time.monotonic() < deadline:
            drained = sess.drain_events("end-file")
            if drained:
                break
            time.sleep(0.02)
        assert drained == 1
    finally:
        sess.close()


def test_reset_fails_pending_command(tmp_path, null_logger):
    sock_path = str(tmp_path / "mpv-reset.sock")
    server = FakeMpvIpcServer(sock_path)

    def slow_handler(msg):
        time.sleep(0.05)
        return default_echo_handler(msg)

    server.set_handler(slow_handler)
    server.start()
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        err_q: queue.Queue = queue.Queue()

        def run():
            try:
                sess.command({"command": ["get_property", "pause"]}, timeout=2.0, request_id=99)
            except BaseException as exc:
                err_q.put(exc)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.02)
        sess.reset()
        exc = err_q.get(timeout=2.0)
        assert isinstance(exc, MPVIPCClosedError)
    finally:
        sess.close()
        server.stop()


def test_reconnect_after_reset(fake_mpv_socket, null_logger):
    sock_path, server = fake_mpv_socket
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        sess.command({"command": ["get_property", "pause"]}, timeout=2.0, request_id=1)
        sess.reset()
        resp = sess.command({"command": ["get_property", "volume"]}, timeout=2.0, request_id=2)
        assert resp["request_id"] == 2
    finally:
        sess.close()


def test_command_timeout(tmp_path, null_logger):
    sock_path = str(tmp_path / "mpv-timeout.sock")
    server = FakeMpvIpcServer(sock_path)
    server.set_handler(lambda _msg: None)
    server.start()
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        with pytest.raises(MPVIPCTimeoutError):
            sess.command({"command": ["get_property", "pause"]}, timeout=0.15, request_id=7)
    finally:
        sess.close()
        server.stop()


def test_malformed_json_ignored_then_valid_reply(tmp_path, null_logger):
    sock_path = str(tmp_path / "mpv-malformed.sock")
    server = FakeMpvIpcServer(sock_path)
    replies = {"sent": False}

    def handler(msg):
        if not replies["sent"]:
            replies["sent"] = True
            with server._lock:
                sock = server._client_sock
            if sock is not None:
                sock.sendall(b"not-json\n{\"broken\":\n")
        return default_echo_handler(msg)

    server.set_handler(handler)
    server.start()
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        resp = sess.command({"command": ["get_property", "pause"]}, timeout=2.0, request_id=11)
        assert resp["request_id"] == 11
    finally:
        sess.close()
        server.stop()


def test_concurrent_commands_thread_safe(fake_mpv_socket, null_logger):
    sock_path, _ = fake_mpv_socket
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    errors: list = []
    results: list = []

    def worker(rid: int):
        try:
            resp = sess.command(
                {"command": ["get_property", "time-pos"]},
                timeout=3.0,
                request_id=rid,
            )
            results.append(resp["request_id"])
        except BaseException as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(1, 6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert not errors
        assert sorted(results) == [1, 2, 3, 4, 5]
    finally:
        sess.close()


def test_socket_eof_fails_pending(tmp_path, null_logger):
    sock_path = str(tmp_path / "mpv-eof.sock")
    server = FakeMpvIpcServer(sock_path)
    server.set_handler(lambda _msg: None)
    server.start()
    sess = MpvJsonIpcSession(sock_path, logger=null_logger)
    try:
        sess._ensure_connected_and_reader()
        server.close_client()
        time.sleep(0.25)
        with pytest.raises(MPVIPCTimeoutError):
            sess.command({"command": ["get_property", "pause"]}, timeout=0.2, request_id=5)
    finally:
        sess.close()
        server.stop()
