"""Best-effort NTP force sync for signage devices (D2.4)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _run_cmd(cmd: List[str], *, timeout: float = 30.0) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode == 0, output or f"exit {result.returncode}"
    except FileNotFoundError:
        return False, "command not found"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except Exception as exc:
        return False, str(exc)


def force_ntp_sync(ntp_server: Optional[str] = None) -> Dict[str, Any]:
    """
    One-shot NTP sync chain (§9):
    timedatectl status → systemd-timesyncd restart → chronyc makestep → ntpdate.
    Never raises; returns structured result for UI.
    """
    server = str(ntp_server or "pool.ntp.org").strip() or "pool.ntp.org"
    steps: List[Dict[str, Any]] = []
    any_ok = False

    chain: List[Tuple[str, List[str]]] = [
        ("timedatectl status", ["timedatectl", "status"]),
        ("systemd-timesyncd restart", ["sudo", "-n", "systemctl", "restart", "systemd-timesyncd"]),
        ("chronyc makestep", ["sudo", "-n", "chronyc", "-a", "makestep"]),
    ]

    ntpdate_bin = shutil.which("ntpdate")
    if ntpdate_bin:
        chain.append((f"ntpdate {server}", ["sudo", "-n", ntpdate_bin, "-u", server]))

    for label, cmd in chain:
        ok, detail = _run_cmd(cmd)
        steps.append({"step": label, "ok": ok, "detail": detail[:500]})
        if ok:
            any_ok = True
        else:
            logger.info("NTP sync step skipped/failed", extra={"step": label, "detail": detail[:200]})

    return {
        "success": any_ok,
        "ntp_server": server,
        "steps": steps,
        "message": "NTP sync completed" if any_ok else "NTP sync unavailable (best-effort)",
    }
