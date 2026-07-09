"""Unit tests for ScheduleService (backlog T-SCH)."""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timezone

import pytest

from dsign.models import ScheduleException, ScheduleRule
from dsign.services.schedule_service import ScheduleService, ScheduleValidationError


def _weekly_rule_payload(playlist_id: int, **overrides):
    base = {
        "playlist_id": playlist_id,
        "days_of_week": 0b0111110,  # Mon–Fri
        "start_time": "09:00",
        "end_time": "18:00",
        "repeat_type": "weekly",
        "priority": 5,
    }
    base.update(overrides)
    return base


def test_parse_rule_data_monthly_requires_valid_from(schedule_db):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)

    with pytest.raises(ScheduleValidationError, match="valid_from is required"):
        svc.parse_rule_data(
            _weekly_rule_payload(playlist.id, repeat_type="monthly"),
            partial=False,
        )


def test_monthly_matches_anchor_day_of_month(schedule_db):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)
    rule = ScheduleRule(
        playlist_id=playlist.id,
        days_of_week=127,
        start_time=dt_time(9, 0),
        end_time=dt_time(18, 0),
        repeat_type="monthly",
        valid_from=date(2026, 1, 15),
        enabled=True,
    )

    assert svc._monthly_matches(rule, date(2026, 7, 15)) is True
    assert svc._monthly_matches(rule, date(2026, 7, 14)) is False
    assert svc._monthly_matches(rule, date(2026, 2, 15)) is True


def test_expand_week_includes_weekly_rule(schedule_db, monkeypatch):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)
    monkeypatch.setattr(
        "dsign.services.schedule_service.local_now",
        lambda _ss=None: datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc),
    )

    rule = svc.create_rule(_weekly_rule_payload(playlist.id))
    payload = svc.expand_week("2026-07-07")

    assert payload["week_start"] == "2026-07-06"  # Monday of ISO week containing 2026-07-07
    dates = {inst["date"] for inst in payload["instances"] if inst["rule_id"] == rule.id}
    assert "2026-07-08" in dates  # Wednesday in that ISO week
    assert "2026-07-12" not in dates  # Sunday not in Mon–Fri mask


def test_expand_month_includes_monthly_rule(schedule_db, monkeypatch):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)
    monkeypatch.setattr(
        "dsign.services.schedule_service.local_now",
        lambda _ss=None: datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc),
    )

    rule = svc.create_rule(
        _weekly_rule_payload(
            playlist.id,
            repeat_type="monthly",
            valid_from="2026-07-15",
            days_of_week=127,
        )
    )
    payload = svc.expand_month("2026-07-01")

    assert payload["month_start"] == "2026-07-01"
    month_dates = {inst["date"] for inst in payload["instances"] if inst["rule_id"] == rule.id}
    assert month_dates == {"2026-07-15"}


def test_exception_skips_rule_instance(schedule_db, monkeypatch):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)
    monkeypatch.setattr(
        "dsign.services.schedule_service.local_now",
        lambda _ss=None: datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc),
    )

    rule = svc.create_rule(_weekly_rule_payload(playlist.id))
    skip_day = "2026-07-08"
    svc.add_exception(rule.id, skip_day)

    payload = svc.expand_week("2026-07-07")
    dates = {inst["date"] for inst in payload["instances"] if inst["rule_id"] == rule.id}
    assert skip_day not in dates

    assert svc.remove_exception(rule.id, skip_day) is True
    payload2 = svc.expand_week("2026-07-07")
    dates2 = {inst["date"] for inst in payload2["instances"] if inst["rule_id"] == rule.id}
    assert skip_day in dates2


def test_batch_mutate_create_update_archive(schedule_db):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)

    result = svc.batch_mutate(
        create=[_weekly_rule_payload(playlist.id, start_time="08:00", end_time="12:00")],
    )
    assert result["success"] is True
    assert len(result["created"]) == 1
    rule_id = result["created"][0]["id"]

    result2 = svc.batch_mutate(
        update=[{"id": rule_id, "start_time": "10:00"}],
        archive=[rule_id],
    )
    assert result2["success"] is True
    assert result2["updated"][0]["start_time"] == "10:00"
    assert rule_id in result2["archived"]
    assert svc.get_rule(rule_id) is None


def test_add_exception_idempotent(schedule_db):
    _app, session, _user, playlist = schedule_db
    svc = ScheduleService(session)
    rule = svc.create_rule(_weekly_rule_payload(playlist.id))

    row1 = svc.add_exception(rule.id, "2026-07-10")
    row2 = svc.add_exception(rule.id, "2026-07-10")

    assert row1.id == row2.id
    count = session.query(ScheduleException).filter_by(rule_id=rule.id).count()
    assert count == 1
