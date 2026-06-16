"""External idle logo viewer (imv via systemd) for Wayland/labwc mode."""

from __future__ import annotations

import subprocess
from typing import Optional

from .playback_constants import PlaybackConstants


class LogoViewer:
    def __init__(self, logger=None) -> None:
        self.logger = logger

    @staticmethod
    def enabled() -> bool:
        return PlaybackConstants.is_wayland_backend()

    def reload(self) -> bool:
        """Ask systemd to restart the logo viewer unit."""
        if not self.enabled():
            return False
        unit = PlaybackConstants.LOGO_SYSTEMD_UNIT
        for cmd in (
            ["sudo", "-n", "systemctl", "restart", unit],
            ["systemctl", "--user", "restart", unit],
        ):
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=15.0, check=False
                )
                if r.returncode == 0:
                    return True
            except Exception as exc:
                if self.logger:
                    self.logger.debug(
                        "logo viewer reload attempt failed",
                        extra={"cmd": cmd, "error": str(exc)},
                    )
        return False

    def is_active(self) -> Optional[bool]:
        if not self.enabled():
            return None
        unit = PlaybackConstants.LOGO_SYSTEMD_UNIT
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
            return None
