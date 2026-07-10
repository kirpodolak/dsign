"""Adaptive MPV restart coalesce (backlog H-COAL)."""

from __future__ import annotations

import pytest

from dsign.services.mpv_restart_coalesce import (
    DEFAULT_MPV_RESTART_COALESCE_MAX_SEC,
    DEFAULT_MPV_RESTART_COALESCE_SEC,
    adaptive_restart_coalesce_sec,
    coalesce_base_sec,
    coalesce_max_sec,
)


def test_coalesce_base_sec_default():
    assert coalesce_base_sec() == DEFAULT_MPV_RESTART_COALESCE_SEC


def test_coalesce_base_sec_env(monkeypatch):
    monkeypatch.setenv("DSIGN_MPV_RESTART_COALESCE_SEC", "12")
    assert coalesce_base_sec() == 12.0


def test_coalesce_max_sec_default():
    assert coalesce_max_sec() == DEFAULT_MPV_RESTART_COALESCE_MAX_SEC


def test_adaptive_restart_coalesce_no_streak():
    assert adaptive_restart_coalesce_sec(ipc_fail_streak=0) == DEFAULT_MPV_RESTART_COALESCE_SEC


def test_adaptive_restart_coalesce_scales_with_streak(monkeypatch):
    monkeypatch.setenv("DSIGN_MPV_RESTART_COALESCE_SEC", "8")
    monkeypatch.setenv("DSIGN_MPV_RESTART_COALESCE_MAX_SEC", "120")
    assert adaptive_restart_coalesce_sec(ipc_fail_streak=1) == 16.0
    assert adaptive_restart_coalesce_sec(ipc_fail_streak=2) == 32.0
    assert adaptive_restart_coalesce_sec(ipc_fail_streak=3) == 64.0


def test_adaptive_restart_coalesce_capped(monkeypatch):
    monkeypatch.setenv("DSIGN_MPV_RESTART_COALESCE_SEC", "8")
    monkeypatch.setenv("DSIGN_MPV_RESTART_COALESCE_MAX_SEC", "30")
    assert adaptive_restart_coalesce_sec(ipc_fail_streak=5) == 30.0


def test_mpv_manager_restart_coalesce_uses_ipc_streak(null_logger):
    from dsign.services.mpv_management import MPVManager

    mgr = MPVManager(
        logger=null_logger,
        socketio=None,
        upload_folder="/tmp",
        mpv_socket="/tmp/mpv.sock",
    )
    mgr.set_playback_session_active(True)
    mgr._playback_ipc_fail_streak = 2

    assert mgr._restart_coalesce_window_sec() == 32.0
