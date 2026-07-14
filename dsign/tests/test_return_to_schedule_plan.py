"""return_to_schedule must plan sync and enqueue play without waiting on loadfile."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService


def _svc_for_return() -> PlaybackService:
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = MagicMock()
    svc._app.app_context = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=None),
            __exit__=MagicMock(return_value=False),
        )
    )
    svc._log_error = MagicMock()
    svc._log_info = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0

    row = MagicMock()
    row.source = "manual"
    session = MagicMock()
    session.query.return_value.get.return_value = row

    db = MagicMock()
    db.session = session
    svc.db_session = db
    svc._schedule_engine = MagicMock()
    return svc


def test_return_to_schedule_enqueues_play_from_plan():
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = ("play", 42, 7)

    enqueued = {}

    def _enq(pid, **kwargs):
        enqueued["pid"] = pid
        enqueued["kwargs"] = kwargs
        return {"accepted": True, "playlist_id": pid}

    svc.enqueue_play = _enq  # type: ignore[method-assign]
    svc.enqueue_stop = MagicMock(return_value=True)
    svc.enqueue_schedule_evaluate = MagicMock(return_value=True)

    t0 = time.monotonic()
    ok = PlaybackService.return_to_schedule(svc)
    elapsed = time.monotonic() - t0

    assert ok is True
    assert elapsed < 0.5
    assert enqueued["pid"] == 42
    assert enqueued["kwargs"]["source"] == "schedule"
    assert enqueued["kwargs"]["rule_id"] == 7
    svc.enqueue_schedule_evaluate.assert_not_called()
    svc.enqueue_stop.assert_not_called()


def test_return_to_schedule_stops_when_no_active_slot():
    """Manual leave with empty schedule must halt mpv (not a no-op evaluate)."""
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = None
    svc.enqueue_play = MagicMock()
    svc.enqueue_stop = MagicMock(return_value=True)
    svc.enqueue_schedule_evaluate = MagicMock(return_value=True)

    assert PlaybackService.return_to_schedule(svc) is True
    svc.enqueue_stop.assert_called_once_with(source="schedule")
    svc.enqueue_play.assert_not_called()
    svc.enqueue_schedule_evaluate.assert_not_called()


def test_return_to_schedule_stop_action_uses_enqueue_stop():
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = ("stop",)
    svc.enqueue_stop = MagicMock(return_value=True)

    assert PlaybackService.return_to_schedule(svc) is True
    svc.enqueue_stop.assert_called_once_with(source="schedule")


def test_schedule_apply_lock_does_not_cover_play():
    """Planning under lock must finish even if another play is 'in flight'."""
    from dsign.services.schedule_engine import ScheduleEngine

    playback = MagicMock()
    playback._app = None
    session = MagicMock(spec=["query", "remove", "add", "commit", "rollback"])
    playback.db_session = session
    row = MagicMock()
    row.source = "idle"
    row.rule_id = None
    row.playlist_id = None
    session.query.return_value.get.return_value = row

    rule = MagicMock()
    rule.id = 1
    rule.playlist_id = 9
    schedule = MagicMock()
    schedule.find_active_rule_candidates.return_value = [rule]

    engine = ScheduleEngine(playback, schedule, logger=MagicMock())
    release = {"go": False}
    play_entered = {"n": 0}

    def _slow_play(*_a, **_k):
        play_entered["n"] += 1
        while not release["go"]:
            time.sleep(0.01)
        return True

    playback.play.side_effect = _slow_play

    # Start evaluate in background (play will block).
    import threading

    t = threading.Thread(target=lambda: engine.evaluate_and_apply(), daemon=True)
    t.start()
    for _ in range(50):
        if play_entered["n"]:
            break
        time.sleep(0.02)
    assert play_entered["n"] == 1

    # Plan must still succeed while play holds — lock does not wrap play.
    t0 = time.monotonic()
    action = engine.plan_action(ignore_manual=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5
    assert action == ("play", 9, 1)

    release["go"] = True
    t.join(timeout=2.0)
