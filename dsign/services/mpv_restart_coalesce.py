"""Adaptive MPV systemd restart coalesce window (backlog H-COAL)."""

from __future__ import annotations

import os

DEFAULT_MPV_RESTART_COALESCE_SEC = 8.0
DEFAULT_MPV_RESTART_COALESCE_MAX_SEC = 60.0


def coalesce_base_sec() -> float:
    raw = (os.getenv("DSIGN_MPV_RESTART_COALESCE_SEC") or "").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_MPV_RESTART_COALESCE_SEC


def coalesce_max_sec() -> float:
    raw = (os.getenv("DSIGN_MPV_RESTART_COALESCE_MAX_SEC") or "").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_MPV_RESTART_COALESCE_MAX_SEC


def adaptive_restart_coalesce_sec(
    *,
    ipc_fail_streak: int = 0,
    playback_active: bool = False,
) -> float:
    """
    Scale coalesce window with recent IPC failure streak.

    Streak 0 → base; each failure doubles up to 4 steps, capped at max.
    During active playback the window never drops below base.
    """
    base = coalesce_base_sec()
    cap = max(base, coalesce_max_sec())
    streak = max(0, int(ipc_fail_streak))
    if streak <= 0:
        return base
    scaled = base * (2 ** min(streak, 4))
    if playback_active:
        scaled = max(base, scaled)
    return min(cap, max(0.0, scaled))
