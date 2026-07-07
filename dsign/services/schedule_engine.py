"""ScheduleEngine — periodic evaluate + play/stop (D2.2)."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from threading import Lock, Timer
from typing import Any, Optional, Union

from ..models import PlaybackStatus, ScheduleRule
from .schedule_time import local_now


class ScheduleEngine:
    TICK_SEC = 30.0

    def __init__(
        self,
        playback_service: Any,
        schedule_service: Any,
        *,
        settings_service: Optional[Any] = None,
        logger: Optional[Union[logging.Logger, Any]] = None,
    ) -> None:
        self.playback = playback_service
        self.schedule_service = schedule_service
        self.settings_service = settings_service
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self._timer: Optional[Timer] = None
        self._running = False
        self._lock = Lock()

    def _app_context(self):
        app = getattr(self.playback, "_app", None)
        if app is not None:
            return app.app_context()
        return nullcontext()

    def _db_session(self):
        db_obj = getattr(self.playback, "db_session", None)
        return getattr(db_obj, "session", db_obj)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self.logger.info("ScheduleEngine started", extra={"tick_sec": self.TICK_SEC})
        self._schedule_tick()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            timer = self._timer
            self._timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _schedule_tick(self) -> None:
        with self._lock:
            if not self._running:
                return
        try:
            self.evaluate_and_apply()
        except Exception as exc:
            self.logger.error(
                "ScheduleEngine tick failed",
                extra={"error": str(exc), "type": type(exc).__name__},
            )
        with self._lock:
            if not self._running:
                return
            self._timer = Timer(self.TICK_SEC, self._schedule_tick)
            self._timer.daemon = True
            self._timer.start()

    def evaluate_now(self) -> Optional[ScheduleRule]:
        with self._app_context():
            return self.schedule_service.find_active_rule(local_now(self.settings_service))

    def evaluate_and_apply(self, *, ignore_manual: bool = False) -> None:
        with self._app_context():
            self._evaluate(ignore_manual=ignore_manual)

    def _playback_row(self) -> Optional[PlaybackStatus]:
        session = self._db_session()
        if session is None:
            return None
        return session.query(PlaybackStatus).get(1)

    def _get_current_source(self) -> str:
        row = self._playback_row()
        if row is None:
            return "idle"
        return str(row.source or "idle")

    def _get_current_rule_id(self) -> Optional[int]:
        row = self._playback_row()
        if row is None or row.rule_id is None:
            return None
        return int(row.rule_id)

    def _get_current_playlist_id(self) -> Optional[int]:
        row = self._playback_row()
        if row is None or row.playlist_id is None:
            return None
        return int(row.playlist_id)

    def _evaluate(self, *, ignore_manual: bool = False) -> None:
        current_source = self._get_current_source()
        if not ignore_manual and current_source in ("override", "manual"):
            return

        now = local_now(self.settings_service)
        candidates = self.schedule_service.find_active_rule_candidates(now)
        active_rule = candidates[0] if candidates else None

        if len(candidates) > 1:
            self.logger.info(
                "schedule_conflict_resolved",
                extra={
                    "chosen_rule_id": int(active_rule.id),
                    "skipped_rule_ids": [int(r.id) for r in candidates[1:]],
                    "now": now.isoformat(),
                },
            )

        if active_rule is not None:
            current_rule_id = self._get_current_rule_id()
            current_playlist = self._get_current_playlist_id()
            if (
                current_source == "schedule"
                and current_rule_id == int(active_rule.id)
                and current_playlist == int(active_rule.playlist_id)
            ):
                return
            self.playback.play(
                int(active_rule.playlist_id),
                source="schedule",
                rule_id=int(active_rule.id),
            )
            return

        if self._get_current_source() == "schedule":
            self.playback.stop(source="schedule")
