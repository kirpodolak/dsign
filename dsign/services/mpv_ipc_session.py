"""
Enterprise-style mpv JSON IPC over Unix socket.

One long-lived connection per application process, with a dedicated reader thread
that demultiplexes command replies by ``request_id``. mpv may interleave
property-change events between replies; we ignore non-reply lines without
blocking the playback controller.

This avoids opening/closing a new socket for every ``get_property``, which
reduces mpv accept/teardown churn and IPC overhead under dashboard + playlist
polling load.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from typing import Any, Dict, List, Optional


class MPVIPCClosedError(ConnectionError):
    """mpv closed the unix socket or the session was reset before reply."""


class MPVIPCTimeoutError(TimeoutError):
    """No JSON command reply matched our request within the deadline."""


class MpvJsonIpcSession:
    """
    Thread-safe JSON IPC client for a single mpv instance.

    - Writer path: ``command()`` registers a pending queue, sends one JSON line.
    - Reader thread: parses newline-delimited JSON; routes replies with
      ``request_id`` + ``error`` key to the matching queue; skips pure events.
    """

    def __init__(
        self,
        socket_path: str,
        *,
        logger: Any,
        recv_chunk_size: int = 65536,
    ) -> None:
        self.socket_path = socket_path
        self.logger = logger
        self._recv_chunk_size = recv_chunk_size

        self._sock: Optional[socket.socket] = None
        self._conn_lock = threading.Lock()
        self._pending: Dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()

        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._buf = b""
        self._event_queues: Dict[str, queue.Queue] = {}
        self._event_queues_lock = threading.Lock()

    def close(self) -> None:
        """Stop reader and release socket (process shutdown or service restart)."""
        self._reader_stop.set()
        self._close_socket_unlocked(fail_pending=False)
        t = self._reader_thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        self._reader_thread = None
        self._reader_stop.clear()

    def reset(self) -> None:
        """
        Drop connection and fail waiters (after IPC error or external mpv restart).
        Next ``command()`` reconnects.
        """
        self._close_socket_unlocked(fail_pending=True)

    def command(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float,
        request_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send one IPC command and wait for mpv's JSON reply for this ``request_id``.

        Raises:
            MPVIPCTimeoutError: deadline exceeded without matching reply.
            MPVIPCClosedError: disconnect during operation or session reset.
            ConnectionRefusedError / FileNotFoundError: socket missing.
        """
        ipc_request_id = (
            int(request_id)
            if request_id is not None
            else (int(time.time() * 1_000_000) & 0x7FFFFFFF)
        )
        body = dict(payload)
        body["request_id"] = ipc_request_id

        q: queue.Queue = queue.Queue(maxsize=8)
        with self._pending_lock:
            self._pending[ipc_request_id] = q

        line = json.dumps(body, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")

        try:
            self._ensure_connected_and_reader()
            with self._conn_lock:
                if self._sock is None:
                    raise MPVIPCClosedError("mpv IPC socket not connected")
                try:
                    self._sock.sendall(data)
                except OSError as se:
                    self._close_socket_unlocked(fail_pending=False)
                    raise MPVIPCClosedError(f"mpv IPC reset during send: {se}") from se
        except BaseException:
            with self._pending_lock:
                self._pending.pop(ipc_request_id, None)
            raise

        result: Any = None
        try:
            try:
                result = q.get(timeout=timeout)
            except queue.Empty:
                raise MPVIPCTimeoutError(
                    "No command reply from MPV (events only or empty buffer)"
                )
        finally:
            with self._pending_lock:
                self._pending.pop(ipc_request_id, None)

        if isinstance(result, BaseException):
            raise result
        if not isinstance(result, dict):
            raise MPVIPCClosedError("mpv IPC unexpected reply payload")
        return result

    def subscribe_event(self, event_name: str) -> queue.Queue:
        """Register interest in mpv client events (e.g. end-file)."""
        name = str(event_name or "").strip()
        with self._event_queues_lock:
            q = self._event_queues.get(name)
            if q is None:
                q = queue.Queue(maxsize=64)
                self._event_queues[name] = q
            return q

    def wait_event(self, event_name: str, *, timeout: float) -> Optional[Dict[str, Any]]:
        """Block until mpv emits ``event_name`` or timeout."""
        q = self.subscribe_event(event_name)
        try:
            raw = q.get(timeout=max(0.0, float(timeout)))
        except queue.Empty:
            return None
        return raw if isinstance(raw, dict) else None

    def commands_batch(
        self,
        items: List[tuple[int, Dict[str, Any]]],
        *,
        timeout: float,
    ) -> List[Dict[str, Any]]:
        """
        Send several IPC commands and collect replies (order matches ``items``).

        Replies may arrive interleaved from mpv; each ``request_id`` has its own wait queue.
        Raises on first per-command deadline miss (drops any still-registered ids for this batch).
        """
        if not items:
            return []

        qs: List[queue.Queue] = []
        ids: List[int] = []
        chunks: List[bytes] = []

        try:
            for ipc_request_id, payload in items:
                q: queue.Queue = queue.Queue(maxsize=8)
                qs.append(q)
                ids.append(ipc_request_id)
                with self._pending_lock:
                    self._pending[ipc_request_id] = q
                body = dict(payload)
                body["request_id"] = ipc_request_id
                chunks.append((json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8"))
        except BaseException:
            for ipc_request_id in ids:
                with self._pending_lock:
                    self._pending.pop(ipc_request_id, None)
            raise

        try:
            self._ensure_connected_and_reader()
            with self._conn_lock:
                if self._sock is None:
                    raise MPVIPCClosedError("mpv IPC socket not connected")
                try:
                    self._sock.sendall(b"".join(chunks))
                except OSError as se:
                    self._close_socket_unlocked(fail_pending=False)
                    raise MPVIPCClosedError(f"mpv IPC reset during send: {se}") from se
        except BaseException:
            for ipc_request_id in ids:
                with self._pending_lock:
                    self._pending.pop(ipc_request_id, None)
            raise

        n = len(qs)
        per_cmd = max(0.05, float(timeout))
        # mpv may answer batched get_property sequentially; a flat +0.45s/n almost always
        # times out on Pi under decode load (5 props @ 3s → 4.8s total was too tight).
        budget = min(60.0, max(per_cmd + 1.0, per_cmd * float(n)))
        deadline = time.monotonic() + budget
        results: List[Dict[str, Any]] = []
        try:
            for q in qs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MPVIPCTimeoutError(
                        "No command reply from MPV (events only or empty buffer)"
                    )
                try:
                    raw = q.get(timeout=remaining)
                except queue.Empty:
                    raise MPVIPCTimeoutError(
                        "No command reply from MPV (events only or empty buffer)"
                    )
                if isinstance(raw, BaseException):
                    raise raw
                if not isinstance(raw, dict):
                    raise MPVIPCClosedError("mpv IPC unexpected reply payload")
                results.append(raw)
            return results
        finally:
            for ipc_request_id in ids:
                with self._pending_lock:
                    self._pending.pop(ipc_request_id, None)

    # --- internals ---

    def _fail_all_pending(self, exc: BaseException) -> None:
        with self._pending_lock:
            items = list(self._pending.items())
            self._pending.clear()
        for _, q in items:
            try:
                q.put_nowait(exc)
            except queue.Full:
                pass

    def _close_socket_unlocked(self, *, fail_pending: bool) -> None:
        if fail_pending:
            self._fail_all_pending(MPVIPCClosedError("mpv IPC session closed"))
        sock: Optional[socket.socket] = None
        with self._conn_lock:
            sock = self._sock
            self._sock = None
        self._buf = b""
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _ensure_connected_and_reader(self) -> None:
        with self._conn_lock:
            if self._sock is not None:
                self._start_reader_unlocked()
                return
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(15.0)
                s.connect(self.socket_path)
                s.settimeout(None)
            except (ConnectionRefusedError, FileNotFoundError):
                raise
            except OSError as e:
                raise MPVIPCClosedError(f"mpv IPC connect failed: {e}") from e
            self._sock = s
            self._buf = b""
            self._start_reader_unlocked()

    def _start_reader_unlocked(self) -> None:
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return
        self._reader_stop.clear()
        t = threading.Thread(
            target=self._reader_loop,
            name="mpv-json-ipc-reader",
            daemon=True,
        )
        self._reader_thread = t
        t.start()

    def _reader_loop(self) -> None:
        while not self._reader_stop.is_set():
            with self._conn_lock:
                sock = self._sock
            if sock is None:
                time.sleep(0.03)
                continue
            try:
                with self._pending_lock:
                    pending = bool(self._pending)
                sock.settimeout(0.15 if pending else 0.6)
                chunk = sock.recv(self._recv_chunk_size)
            except socket.timeout:
                continue
            except OSError as e:
                self.logger.debug(
                    "mpv IPC reader socket error",
                    extra={"operation": "MpvJsonIpcSession", "error": str(e)},
                )
                self._close_socket_unlocked(fail_pending=True)
                continue

            if chunk == b"":
                self.logger.debug(
                    "mpv IPC EOF on socket",
                    extra={"operation": "MpvJsonIpcSession"},
                )
                self._close_socket_unlocked(fail_pending=True)
                continue

            self._feed_lines(chunk)

    def _feed_lines(self, chunk: bytes) -> None:
        self._buf += chunk
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            raw = self._buf[:idx]
            self._buf = self._buf[idx + 1 :]
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if "event" in obj:
                ev_name = str(obj.get("event") or "").strip()
                if ev_name:
                    with self._event_queues_lock:
                        ev_q = self._event_queues.get(ev_name)
                    if ev_q is not None:
                        try:
                            ev_q.put_nowait(obj)
                        except queue.Full:
                            pass
                continue
            if "error" not in obj:
                continue
            rid = obj.get("request_id")
            if rid is None:
                continue
            try:
                rid_i = int(rid)
            except (TypeError, ValueError):
                continue
            with self._pending_lock:
                q = self._pending.get(rid_i)
            if q is None:
                continue
            try:
                q.put_nowait(obj)
            except queue.Full:
                pass
