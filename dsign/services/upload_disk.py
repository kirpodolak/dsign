"""Disk space checks before media upload (backlog H-UPL)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_UPLOAD_DISK_RESERVE_BYTES = 50 * 1024 * 1024  # 50 MiB headroom for OS/logs


def upload_disk_reserve_bytes() -> int:
    raw = (os.getenv("DSIGN_UPLOAD_DISK_RESERVE_BYTES") or "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return DEFAULT_UPLOAD_DISK_RESERVE_BYTES


def check_disk_space_for_upload(
    target_dir: str | Path,
    required_bytes: int,
    *,
    max_file_bytes: int | None = None,
) -> tuple[bool, str | None]:
    """
    Verify free space on the filesystem backing ``target_dir``.

    When ``required_bytes`` is known (>0), require file size + reserve.
    When unknown, require ``max_file_bytes`` + reserve (conservative).
    """
    try:
        usage = shutil.disk_usage(Path(target_dir))
    except OSError:
        return True, None

    reserve = upload_disk_reserve_bytes()
    free = int(usage.free)

    if required_bytes > 0:
        need = int(required_bytes) + reserve
        file_part = int(required_bytes)
    elif max_file_bytes is not None and max_file_bytes > 0:
        need = int(max_file_bytes) + reserve
        file_part = int(max_file_bytes)
    else:
        need = reserve
        file_part = 0

    if free >= need:
        return True, None

    free_mb = free // (1024 * 1024)
    if file_part > 0:
        need_file_mb = file_part // (1024 * 1024)
        return (
            False,
            f"Insufficient disk space: {free_mb} MiB free, need at least {need_file_mb} MiB for this upload",
        )
    return False, f"Insufficient disk space: {free_mb} MiB free"
