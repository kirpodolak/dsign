"""Wayland/labwc stack helpers (compositor health, env)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .playback_constants import PlaybackConstants


class WaylandManager:
    def __init__(self, logger=None) -> None:
        self.logger = logger

    @staticmethod
    def enabled() -> bool:
        return PlaybackConstants.is_wayland_backend()

    @staticmethod
    def wayland_socket_path() -> Path:
        return Path(PlaybackConstants.xdg_runtime_dir()) / PlaybackConstants.wayland_display()

    def compositor_socket_ready(self) -> bool:
        if not self.enabled():
            return False
        return self.wayland_socket_path().is_socket()

    def wait_for_compositor(self, *, timeout_sec: float = 30.0) -> bool:
        if not self.enabled():
            return True
        import time

        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        while time.monotonic() < deadline:
            if self.compositor_socket_ready():
                return True
            time.sleep(0.25)
        return False

    def compositor_unit_active(self) -> bool:
        unit = PlaybackConstants.COMPOSITOR_SYSTEMD_UNIT
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            return r.stdout.strip() == "active"
        except Exception:
            return False

    def log_status(self) -> None:
        if not self.logger or not self.enabled():
            return
        self.logger.info(
            "Wayland stack status",
            extra={
                "wayland_display": PlaybackConstants.wayland_display(),
                "xdg_runtime_dir": PlaybackConstants.xdg_runtime_dir(),
                "socket_ready": self.compositor_socket_ready(),
                "compositor_active": self.compositor_unit_active(),
            },
        )
