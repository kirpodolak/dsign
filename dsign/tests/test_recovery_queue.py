"""Playback recovery queue (backlog H-RQ)."""

from __future__ import annotations

from dsign.services.recovery_queue import RecoveryJob, RecoveryJobKind, RecoveryQueue


def test_recovery_queue_dedupes_same_kind():
    q = RecoveryQueue(max_size=4)
    assert q.enqueue(RecoveryJob(RecoveryJobKind.MPV_SYSTEMD, {"resume_advance": False}))
    assert q.enqueue(RecoveryJob(RecoveryJobKind.MPV_SYSTEMD, {"resume_advance": True}))
    assert len(q) == 1
    job = q.pop()
    assert job is not None
    assert job.kwargs["resume_advance"] is True


def test_recovery_queue_fifo_distinct_kinds():
    q = RecoveryQueue(max_size=4)
    q.enqueue(RecoveryJob(RecoveryJobKind.MPV_SYSTEMD))
    q.enqueue(RecoveryJob(RecoveryJobKind.SLIDESHOW_CRASH))
    assert q.pop().kind == RecoveryJobKind.MPV_SYSTEMD
    assert q.pop().kind == RecoveryJobKind.SLIDESHOW_CRASH


def test_recovery_queue_max_size():
    q = RecoveryQueue(max_size=1)
    assert q.enqueue(RecoveryJob(RecoveryJobKind.MPV_SYSTEMD))
    assert not q.enqueue(RecoveryJob(RecoveryJobKind.SLIDESHOW_CRASH))
