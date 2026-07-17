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
    row.status = "stopped"
    row.rule_id = 3
    session = MagicMock()
    session.query.return_value.get.return_value = row

    db = MagicMock()
    db.session = session
    svc.db_session = db
    svc._schedule_engine = MagicMock()
    svc._playlist_manager = MagicMock()
    return svc


def test_return_to_schedule_bumps_run_id_before_invalidate():
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = None
    svc._playlist_manager._mpv_has_active_media.return_value = False
    svc.enqueue_schedule_evaluate = MagicMock(return_value=True)
    order = []

    def _bump():
        order.append("bump")

    def _inv():
        order.append("invalidate")

    svc._playlist_manager._bump_playback_run_id.side_effect = _bump
    svc._playlist_manager.invalidate_in_flight_play.side_effect = _inv

    assert PlaybackService.return_to_schedule(svc) is True
    assert order == ["bump", "invalidate"]


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


def test_return_to_schedule_stops_when_no_slot_but_content_on_air():
    """Empty schedule + residual A1/A2 content must halt mpv."""
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = None
    svc._playlist_manager = MagicMock()
    svc._playlist_manager._mpv_has_active_media.return_value = True
    svc.enqueue_play = MagicMock()
    svc.enqueue_stop = MagicMock(return_value=True)
    svc.enqueue_schedule_evaluate = MagicMock(return_value=True)

    assert PlaybackService.return_to_schedule(svc) is True
    svc.enqueue_stop.assert_called_once_with(source="schedule")
    svc.enqueue_play.assert_not_called()
    svc.enqueue_schedule_evaluate.assert_not_called()


def test_return_to_schedule_evaluates_when_already_idle_no_slot():
    """Already on idle logo: evaluate so a newly enabled rule can start."""
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = None
    svc._playlist_manager = MagicMock()
    svc._playlist_manager._mpv_has_active_media.return_value = False
    svc.enqueue_stop = MagicMock(return_value=True)
    svc.enqueue_schedule_evaluate = MagicMock(return_value=True)

    assert PlaybackService.return_to_schedule(svc) is True
    svc.enqueue_schedule_evaluate.assert_called_once_with(ignore_manual=True)
    svc.enqueue_stop.assert_not_called()


def test_return_to_schedule_stop_action_uses_enqueue_stop():
    svc = _svc_for_return()
    svc._schedule_engine.plan_action.return_value = ("stop",)
    svc.enqueue_stop = MagicMock(return_value=True)

    assert PlaybackService.return_to_schedule(svc) is True
    svc.enqueue_stop.assert_called_once_with(source="schedule")


def test_schedule_apply_lock_does_not_cover_play():
    """Planning under lock must finish even if enqueue_play is in flight."""
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

    def _slow_enqueue(*_a, **_k):
        play_entered["n"] += 1
        while not release["go"]:
            time.sleep(0.01)
        return {"accepted": True}

    playback.enqueue_play.side_effect = _slow_enqueue

    import threading

    t = threading.Thread(target=lambda: engine.evaluate_and_apply(), daemon=True)
    t.start()
    for _ in range(50):
        if play_entered["n"]:
            break
        time.sleep(0.02)
    assert play_entered["n"] == 1

    t0 = time.monotonic()
    action = engine.plan_action(ignore_manual=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5
    assert action == ("play", 9, 1)

    release["go"] = True
    t.join(timeout=2.0)
