"""Schedule rules CRUD and week expansion (D2.1 — no ScheduleEngine)."""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import asc

from ..models import PlaybackStatus, Playlist, ScheduleRule
from .schedule_time import local_now


class ScheduleValidationError(ValueError):
    """Invalid schedule rule payload."""


def _parse_hhmm(value: Any, field: str) -> dt_time:
    if value is None:
        raise ScheduleValidationError(f"{field} is required")
    if isinstance(value, dt_time):
        return value
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ScheduleValidationError(f"{field} must be HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        return dt_time(hour=hour, minute=minute)
    except (TypeError, ValueError) as exc:
        raise ScheduleValidationError(f"{field} must be HH:MM") from exc


def _parse_date(value: Any, field: str) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ScheduleValidationError(f"{field} must be YYYY-MM-DD") from exc


def _format_time(value: Optional[dt_time]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%H:%M")


def _format_date(value: Optional[date]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _time_cutoff(now: datetime, *, same_day: bool) -> dt_time:
    return now.time() if same_day else dt_time.min


class ScheduleService:
    def __init__(
        self,
        db_session,
        settings_service: Optional[Any] = None,
        logger: Optional[Union[logging.Logger, Any]] = None,
    ) -> None:
        self.db_session = db_session
        self.settings_service = settings_service
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    def parse_rule_data(self, data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
        if not data and not partial:
            raise ScheduleValidationError("empty payload")

        out: Dict[str, Any] = {}
        if "playlist_id" in data or not partial:
            if "playlist_id" not in data:
                raise ScheduleValidationError("playlist_id is required")
            try:
                out["playlist_id"] = int(data["playlist_id"])
            except (TypeError, ValueError) as exc:
                raise ScheduleValidationError("playlist_id must be an integer") from exc
            if not self.db_session.query(Playlist).get(out["playlist_id"]):
                raise ScheduleValidationError("playlist_id does not exist")

        if "enabled" in data or not partial:
            out["enabled"] = bool(data.get("enabled", True))

        if "days_of_week" in data or not partial:
            try:
                dow = int(data.get("days_of_week", 0))
            except (TypeError, ValueError) as exc:
                raise ScheduleValidationError("days_of_week must be an integer bitmask") from exc
            if dow <= 0:
                raise ScheduleValidationError("days_of_week must be > 0")
            out["days_of_week"] = dow

        if "start_time" in data or not partial:
            out["start_time"] = _parse_hhmm(data.get("start_time"), "start_time")
        if "end_time" in data or not partial:
            out["end_time"] = _parse_hhmm(data.get("end_time"), "end_time")

        if "start_time" in out and "end_time" in out and out["start_time"] >= out["end_time"]:
            raise ScheduleValidationError("start_time must be before end_time")

        if "repeat_type" in data or not partial:
            repeat = str(data.get("repeat_type", "weekly")).strip().lower()
            if repeat not in ("weekly", "once"):
                raise ScheduleValidationError("repeat_type must be weekly or once")
            out["repeat_type"] = repeat

        if "valid_from" in data or not partial:
            out["valid_from"] = _parse_date(data.get("valid_from"), "valid_from")
        if "valid_until" in data or not partial:
            out["valid_until"] = _parse_date(data.get("valid_until"), "valid_until")

        if out.get("repeat_type") == "once" and not partial:
            if out.get("valid_from") is None:
                raise ScheduleValidationError("valid_from is required for repeat_type once")

        if "priority" in data or not partial:
            try:
                priority = int(data.get("priority", 5))
            except (TypeError, ValueError) as exc:
                raise ScheduleValidationError("priority must be an integer") from exc
            if priority < 1 or priority > 10:
                raise ScheduleValidationError("priority must be between 1 and 10")
            out["priority"] = priority

        return out

    def list_rules(self) -> List[ScheduleRule]:
        return (
            self.db_session.query(ScheduleRule)
            .filter(ScheduleRule.archived_at.is_(None))
            .order_by(asc(ScheduleRule.priority), asc(ScheduleRule.id))
            .all()
        )

    def get_rule(self, rule_id: int) -> Optional[ScheduleRule]:
        rule = self.db_session.query(ScheduleRule).get(int(rule_id))
        if rule is None or rule.archived_at is not None:
            return None
        return rule

    def create_rule(self, data: Dict[str, Any]) -> ScheduleRule:
        parsed = self.parse_rule_data(data, partial=False)
        rule = ScheduleRule(**parsed)
        self.db_session.add(rule)
        self.db_session.commit()
        return rule

    def update_rule(self, rule_id: int, data: Dict[str, Any]) -> ScheduleRule:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise ScheduleValidationError("rule not found")
        merged = {
            "playlist_id": rule.playlist_id,
            "enabled": rule.enabled,
            "days_of_week": rule.days_of_week,
            "start_time": rule.start_time,
            "end_time": rule.end_time,
            "repeat_type": rule.repeat_type,
            "valid_from": rule.valid_from,
            "valid_until": rule.valid_until,
            "priority": rule.priority,
        }
        merged.update(data or {})
        parsed = self.parse_rule_data(merged, partial=False)
        for key, value in parsed.items():
            setattr(rule, key, value)
        rule.updated_at = datetime.utcnow()
        self.db_session.commit()
        return rule

    def archive_rule(self, rule_id: int) -> ScheduleRule:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise ScheduleValidationError("rule not found")
        rule.archived_at = datetime.utcnow()
        rule.updated_at = datetime.utcnow()
        self.db_session.commit()
        return rule

    def toggle_rule(self, rule_id: int) -> ScheduleRule:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise ScheduleValidationError("rule not found")
        rule.enabled = not bool(rule.enabled)
        rule.updated_at = datetime.utcnow()
        self.db_session.commit()
        return rule

    def rule_to_dict(self, rule: ScheduleRule) -> Dict[str, Any]:
        playlist = rule.playlist or self.db_session.query(Playlist).get(rule.playlist_id)
        return {
            "id": rule.id,
            "playlist_id": rule.playlist_id,
            "playlist_name": playlist.name if playlist else None,
            "enabled": bool(rule.enabled),
            "days_of_week": int(rule.days_of_week or 0),
            "start_time": _format_time(rule.start_time),
            "end_time": _format_time(rule.end_time),
            "repeat_type": rule.repeat_type,
            "valid_from": _format_date(rule.valid_from),
            "valid_until": _format_date(rule.valid_until),
            "priority": int(rule.priority or 5),
            "created_at": rule.created_at.isoformat() if rule.created_at else None,
            "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
        }

    def _week_dates(self, anchor: date) -> List[date]:
        monday = anchor - timedelta(days=anchor.weekday())
        return [monday + timedelta(days=i) for i in range(7)]

    def _rule_applies_on_date(self, rule: ScheduleRule, day: date) -> bool:
        if rule.archived_at is not None:
            return False
        if rule.repeat_type == "once":
            return rule.valid_from is not None and day == rule.valid_from
        bit = 1 << day.weekday()
        if not (int(rule.days_of_week or 0) & bit):
            return False
        if rule.valid_from and day < rule.valid_from:
            return False
        if rule.valid_until and day > rule.valid_until:
            return False
        return True

    def _is_expired(self, rule: ScheduleRule, day: date) -> bool:
        if rule.valid_until and day > rule.valid_until:
            return True
        if rule.repeat_type == "once" and rule.valid_from and day > rule.valid_from:
            return True
        return False

    def _times_overlap(self, a_start: dt_time, a_end: dt_time, b_start: dt_time, b_end: dt_time) -> bool:
        return a_start < b_end and b_start < a_end

    def _annotate_conflicts(self, instances: List[Dict[str, Any]]) -> None:
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for inst in instances:
            by_date.setdefault(inst["date"], []).append(inst)
        for group in by_date.values():
            for inst in group:
                count = 0
                for other in group:
                    if other["rule_id"] == inst["rule_id"] and other["date"] == inst["date"]:
                        continue
                    if self._times_overlap(
                        _parse_hhmm(inst["start_time"], "start_time"),
                        _parse_hhmm(inst["end_time"], "end_time"),
                        _parse_hhmm(other["start_time"], "start_time"),
                        _parse_hhmm(other["end_time"], "end_time"),
                    ):
                        count += 1
                inst["has_conflict"] = count > 0

    def _playback_row(self) -> Optional[PlaybackStatus]:
        return self.db_session.query(PlaybackStatus).get(1)

    def _progress_percent(
        self,
        *,
        now: datetime,
        day: date,
        start: dt_time,
        end: dt_time,
        is_playing: bool,
    ) -> int:
        if not is_playing:
            return 0
        tz = now.tzinfo
        start_dt = datetime.combine(day, start, tzinfo=tz)
        end_dt = datetime.combine(day, end, tzinfo=tz)
        total = (end_dt - start_dt).total_seconds()
        if total <= 0:
            return 0
        elapsed = (now - start_dt).total_seconds()
        return int(max(0, min(100, round(100 * elapsed / total))))

    def _instance_dict(
        self,
        rule: ScheduleRule,
        day: date,
        *,
        now: datetime,
        playback: Optional[PlaybackStatus],
    ) -> Dict[str, Any]:
        playlist = rule.playlist or self.db_session.query(Playlist).get(rule.playlist_id)
        is_playing_now = False
        if playback is not None:
            is_playing_now = (
                (playback.source or "idle") == "schedule"
                and playback.rule_id == rule.id
                and day == now.date()
            )
        return {
            "id": f"{rule.id}-{day.isoformat()}",
            "rule_id": rule.id,
            "playlist_id": rule.playlist_id,
            "playlist_name": playlist.name if playlist else None,
            "date": day.isoformat(),
            "day_of_week": day.weekday(),
            "days_of_week": int(rule.days_of_week or 0),
            "repeat_type": rule.repeat_type,
            "valid_from": _format_date(rule.valid_from),
            "valid_until": _format_date(rule.valid_until),
            "start_time": _format_time(rule.start_time),
            "end_time": _format_time(rule.end_time),
            "priority": int(rule.priority or 5),
            "is_active": bool(rule.enabled),
            "is_expired": self._is_expired(rule, day),
            "is_playing_now": is_playing_now,
            "progress_percent": self._progress_percent(
                now=now,
                day=day,
                start=rule.start_time,
                end=rule.end_time,
                is_playing=is_playing_now,
            ),
            "has_conflict": False,
        }

    def expand_week(self, anchor: Union[str, date]) -> Dict[str, Any]:
        if isinstance(anchor, str):
            anchor_date = _parse_date(anchor, "date")
        else:
            anchor_date = anchor
        if anchor_date is None:
            raise ScheduleValidationError("date is required")

        now = local_now(self.settings_service)
        playback = self._playback_row()
        week_dates = self._week_dates(anchor_date)
        rules = self.list_rules()
        instances: List[Dict[str, Any]] = []
        for rule in rules:
            for day in week_dates:
                if not self._rule_applies_on_date(rule, day):
                    continue
                instances.append(self._instance_dict(rule, day, now=now, playback=playback))

        self._annotate_conflicts(instances)
        instances.sort(key=lambda x: (x["date"], x["start_time"], x["priority"], x["rule_id"]))

        return {
            "week_start": week_dates[0].isoformat(),
            "week_end": week_dates[-1].isoformat(),
            "instances": instances,
        }

    def find_active_rule(self, now: Optional[datetime] = None) -> Optional[ScheduleRule]:
        now = now or local_now(self.settings_service)
        candidates: List[ScheduleRule] = []
        for rule in self.list_rules():
            if not rule.enabled:
                continue
            if not self._rule_applies_on_date(rule, now.date()):
                continue
            if rule.start_time <= now.time() < rule.end_time:
                candidates.append(rule)
        if not candidates:
            return None
        candidates.sort(key=lambda r: (int(r.priority or 5), int(r.id)))
        return candidates[0]

    def find_next_rule(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        now = now or local_now(self.settings_service)
        best: Optional[tuple] = None
        for offset in range(0, 14):
            day = now.date() + timedelta(days=offset)
            same_day = offset == 0
            cutoff = _time_cutoff(now, same_day=same_day)
            for rule in self.list_rules():
                if not rule.enabled:
                    continue
                if not self._rule_applies_on_date(rule, day):
                    continue
                if rule.start_time <= cutoff:
                    continue
                key = (day, rule.start_time, int(rule.priority or 5), int(rule.id))
                if best is None or key < best[0]:
                    best = (key, rule, day)
        if best is None:
            return None
        _, rule, day = best
        tz = now.tzinfo
        at_dt = datetime.combine(day, rule.start_time, tzinfo=tz)
        return {
            "rule": self.rule_to_dict(rule),
            "at": at_dt.isoformat(),
            "date": day.isoformat(),
            "start_time": _format_time(rule.start_time),
        }

    def schedule_now(self) -> Dict[str, Any]:
        now = local_now(self.settings_service)
        active = self.find_active_rule(now)
        nxt = self.find_next_rule(now)
        return {
            "now": now.isoformat(),
            "active": self.rule_to_dict(active) if active else None,
            "next": nxt,
        }
