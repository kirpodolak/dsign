"""Unit tests for MPVManager._send_command (backlog T-MPV).

These tests focus on:
- guarding `set_property vo` during active playback
- allowing `set_property vo` via `set_vo_property`
- retry + coalesced restart behaviour on transport errors
- retry budget calculation in `_send_command_max_retries`
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

import time

import pytest

from dsign.services.mpv_ipc_session import MPVIPCClosedError
from dsign.services.mpv_management import MPVManager, PlaybackConstants


class DummySession:
    """Fake MpvJsonIpcSession used by MPVManager tests."""

    def __init__(self, behaviours: Sequence[Union[Dict[str, Any], BaseException]]) -> None:
        self._behaviours: List[Union[Dict[str, Any], BaseException]] = list(behaviours)
        self.calls: List[Dict[str, Any]] = []

    def command(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float,
        request_id: int,
    ) -> Dict[str, Any]:
        self.calls.append(
            {
                "payload": dict(payload),
                "timeout": timeout,
                "request_id": request_id,
            }
        )
        if not self._behaviours:
            # Default successful reply if behaviours are exhausted.
            return {"error": "success", "request_id": request_id, "data": None}
        item = self._behaviours.pop(0)
        if isinstance(item, BaseException):
            raise item
        result = dict(item)
        result.setdefault("request_id", request_id)
        result.setdefault("error", "success")
        return result


class TestMPVManager(MPVManager):
    """Subclass MPVManager to stub out systemd and socket interactions."""

    def __init__(self, logger: Any, session: DummySession) -> None:
        super().__init__(logger=logger, socketio=None, upload_folder="/tmp", mpv_socket="/tmp/mpv.sock")
        self._test_session = session
        self.restart_calls: int = 0
        self.wait_calls: List[float] = []
        self.recover_calls: int = 0
        self.check_socket_calls: int = 0

    # IPC/session plumbing
    def _get_ipc_session(self) -> DummySession:  # type: ignore[override]
        return self._test_session

    def _acquire_ipc_lock(self, *, lock_wait: Optional[float] = None, prefer_long: bool = False) -> bool:  # type: ignore[override]
        # Tests do not need real inter-thread locking.
        return True

    def _release_ipc_lock(self) -> None:  # type: ignore[override]
        return None

    # Socket/systemd helpers
    def _mpv_socket_file_exists(self) -> bool:  # type: ignore[override]
        return True

    def _try_recover_socket_without_restart(self) -> bool:  # type: ignore[override]
        self.recover_calls += 1
        return True

    def _check_mpv_socket(self, timeout: float = 0.5) -> bool:  # type: ignore[override]
        self.check_socket_calls += 1
        return True

    def _restart_systemd_service_if_needed(self) -> bool:  # type: ignore[override]
        self.restart_calls += 1
        return True

    def _wait_for_socket(self, timeout: float = 12.0) -> bool:  # type: ignore[override]
        self.wait_calls.append(timeout)
        return True


def test_vo_switch_blocked_during_playback(null_logger):
    """set_property vo is blocked while playback session is active."""
    session = DummySession(
        behaviours=[
            {"error": "success", "data": None},
        ]
    )
    mgr = TestMPVManager(logger=null_logger, session=session)
    mgr.set_playback_session_active(True)

    result = mgr._send_command({"command": ["set_property", "vo", "gpu"]})

    assert result == {"error": "vo switch blocked during playback"}
    # Command must not be sent over IPC when blocked.
    assert session.calls == []


def test_vo_switch_allowed_via_helper(null_logger):
    """set_vo_property bypasses the playback guard (used for logo/audio transitions)."""
    reply = {"error": "success", "data": "gpu"}
    session = DummySession([reply])
    mgr = TestMPVManager(logger=null_logger, session=session)
    mgr.set_playback_session_active(True)

    result = mgr.set_vo_property("gpu-next")

    assert result is not None
    assert result["error"] == "success"
    assert len(session.calls) == 1
    payload = session.calls[0]["payload"]
    assert payload["command"][0] == "set_property"
    assert payload["command"][1] == "vo"


def test_transport_error_triggers_coalesced_restart(null_logger, monkeypatch):
    """MPV transport error leads to at most one systemd restart attempt per _send_command call."""

    # Ensure restart coalesce window is non-zero and predictable.
    monkeypatch.setenv("DSIGN_MPV_RESTART_COALESCE_SEC", "60")

    behaviours = [
        MPVIPCClosedError("socket closed 1"),
        MPVIPCClosedError("socket closed 2"),
        {"error": "success", "data": "ok"},
    ]
    session = DummySession(behaviours)
    mgr = TestMPVManager(logger=null_logger, session=session)

    start = time.time()
    result = mgr._send_command({"command": ["set_property", "pause", True]}, timeout=1.0, max_attempts=3)
    duration = time.time() - start

    # Command eventually succeeds.
    assert result is not None
    assert result.get("error") == "success"
    # Session was called multiple times due to retries.
    assert len(session.calls) >= 2
    # But systemd restart helper is coalesced to a single attempt.
    assert mgr.restart_calls == 1
    # Restart path waits for socket at least once.
    assert mgr.wait_calls
    # Sanity check: test should not hang for long because retry sleeps are bounded.
    assert duration < 10.0


def test_send_command_max_retries_respects_playback_flags(null_logger):
    """_send_command_max_retries chooses retry budget based on command and playback state."""
    session = DummySession([])
    mgr = TestMPVManager(logger=null_logger, session=session)

    # get_property → always 1 attempt
    assert mgr._send_command_max_retries("get_property", "pause") == 1

    # loadfile + active playback → 2 attempts
    mgr.set_playback_session_active(True)
    assert mgr._send_command_max_retries("loadfile", None) == 2

    # set_property during contended playback/network → 1 attempt
    mgr.set_playback_stream_opening(True)
    assert mgr._send_command_max_retries("set_property", "volume") == 1

    # Non-contended set_property / other commands → PlaybackConstants.MAX_RETRIES
    mgr.set_playback_session_active(False)
    mgr.set_playback_stream_opening(False)
    mgr.set_playback_network_active(False)
    assert mgr._send_command_max_retries("set_property", "pause") == PlaybackConstants.MAX_RETRIES
    assert mgr._send_command_max_retries("show-text", None) == PlaybackConstants.MAX_RETRIES

