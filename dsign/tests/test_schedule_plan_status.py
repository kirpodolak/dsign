"""Schedule plan must re-apply when status is idle with ghost source=schedule."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.schedule_engine import ScheduleEngine


def _engine_with_row(row) -> ScheduleEngine:
    playback = MagicMock()
    playback._app = None
    # spec avoids MagicMock.auto-session (engine would read mock.session, not this row).
    session = MagicMock(spec=["query", "remove", "add", "commit", "rollback"])
    playback.db_session = session
    session.query.return_value.get.return_value = row

    rule = MagicMock()
    rule.id = 1
    rule.playlist_id = 9
    schedule = MagicMock()
    schedule.find_active_rule_candidates.return_value = [rule]
    return ScheduleEngine(playback, schedule, logger=MagicMock())


def test_plan_replays_when_schedule_source_but_status_idle():
    row = MagicMock()
    row.source = "schedule"
    row.status = "idle"
    row.rule_id = 1
    row.playlist_id = 9
    engine = _engine_with_row(row)
    assert engine.plan_action(ignore_manual=True) == ("play", 9, 1)


def test_plan_skips_when_schedule_actually_playing():
    row = MagicMock()
    row.source = "schedule"
    row.status = "playing"
    row.rule_id = 1
    row.playlist_id = 9
    engine = _engine_with_row(row)
    assert engine.plan_action(ignore_manual=True) is None
