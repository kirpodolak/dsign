"""Fake mpv JSON IPC peer for unit tests (backlog T-IPC)."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class FakeMpvIpcServer:
    """
    Minimal mpv JSON IPC peer on AF_UNIX.

    Reads newline-delimited commands; optional handler returns reply dicts.
    Can push client events via ``push_event``.
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None
        self._client_sock: Optional[socket.socket] = None
        self._handler: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None
        self._lock = threading.Lock()
        self.received: List[Dict[str, Any]] = []

    def set_handler(
        self,
        handler: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    ) -> None:
        self._handler = handler

    def start(self) -> None:
        path = Path(self.socket_path)
        if path.exists():
            path.unlink()
        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self.socket_path)
        self._server_sock.listen(1)
        self._thread = threading.Thread(target=self._serve, name="fake-mpv-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for sock in (self._client_sock, self._server_sock):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self._client_sock = None
            self._server_sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        path = Path(self.socket_path)
        if path.exists():
            path.unlink()

    def push_event(self, name: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {"event": name}
        if extra:
            payload.update(extra)
        self._send_line(payload)

    def close_client(self) -> None:
        with self._lock:
            if self._client_sock is not None:
                try:
                    self._client_sock.close()
                except OSError:
                    pass
                self._client_sock = None

    def _send_line(self, obj: Dict[str, Any]) -> None:
        with self._lock:
            sock = self._client_sock
        if sock is None:
            return
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        sock.sendall(data)

    def _serve(self) -> None:
        assert self._server_sock is not None
        self._server_sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self._client_sock = client
            self._read_loop(client)

    def _read_loop(self, client: socket.socket) -> None:
        buf = b""
        client.settimeout(0.2)
        while not self._stop.is_set():
            try:
                chunk = client.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if chunk == b"":
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                self.received.append(msg)
                handler = self._handler
                if handler is None:
                    continue
                reply = handler(msg)
                if reply is not None:
                    self._send_line(reply)


def default_echo_handler(msg: Dict[str, Any]) -> Dict[str, Any]:
    rid = msg.get("request_id")
    return {"error": "success", "request_id": rid, "data": msg.get("command")}
