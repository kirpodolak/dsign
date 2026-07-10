"""Playback recovery queue (backlog H-RQ)."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Deque, Dict, Optional


class RecoveryJobKind(str, Enum):
    MPV_SYSTEMD = "mpv_systemd"
    SLIDESHOW_CRASH = "slideshow_crash"


@dataclass(frozen=True)
class RecoveryJob:
    kind: RecoveryJobKind
    kwargs: Dict[str, Any] = field(default_factory=dict)


def recovery_queue_max_size() -> int:
    raw = (os.getenv("DSIGN_RECOVERY_QUEUE_MAX") or "").strip()
    try:
        return max(1, min(32, int(raw)))
    except ValueError:
        return 8


class RecoveryQueue:
    """FIFO recovery jobs with per-kind deduplication (latest kwargs win)."""

    def __init__(self, *, max_size: Optional[int] = None):
        self._lock = Lock()
        self._jobs: Deque[RecoveryJob] = deque()
        self._max_size = recovery_queue_max_size() if max_size is None else int(max_size)

    def enqueue(self, job: RecoveryJob) -> bool:
        with self._lock:
            for index, existing in enumerate(self._jobs):
                if existing.kind == job.kind:
                    self._jobs[index] = job
                    return True
            if len(self._jobs) >= self._max_size:
                return False
            self._jobs.append(job)
            return True

    def pop(self) -> Optional[RecoveryJob]:
        with self._lock:
            if not self._jobs:
                return None
            return self._jobs.popleft()

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)

    def pending_kinds(self) -> list[str]:
        with self._lock:
            return [job.kind.value for job in self._jobs]
