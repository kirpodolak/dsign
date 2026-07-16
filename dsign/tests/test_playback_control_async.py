"""UI control paths must not block on play()/schedule evaluate."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService


def test_return_to_schedule_returns_before_play_finishes():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = MagicMock()
    svc._app.app_context = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=None),
            __exit__=MagicMock(return_value=False),
        )
    )
    svc._schedule_engine = MagicMock()
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
    svc._schedule_engine.plan_action.return_value = ("play", 3, 1)

    started = {"n": 0}
    release = {"go": False}

    def _slow_enq(pid, **kwargs):
        started["n"] += 1

        def _run():
            while not release["go"]:
                time.sleep(0.01)

        import threading

        threading.Thread(target=_run, daemon=True).start()
        return {"accepted": True, "playlist_id": pid}

    svc.enqueue_play = _slow_enq  # type: ignore[method-assign]

    t0 = time.monotonic()
    ok = PlaybackService.return_to_schedule(svc)
    elapsed = time.monotonic() - t0

    assert ok is True
    assert elapsed < 0.5, f"return_to_schedule blocked for {elapsed:.2f}s"
    assert started["n"] == 1
    release["go"] = True


def test_enqueue_play_returns_immediately():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager = MagicMock()
    svc._log_warning = MagicMock()

    release = {"go": False}
    entered = {"n": 0}

    def _slow_play(*_a, **_k):
        entered["n"] += 1
        while not release["go"]:
            time.sleep(0.01)
        return True

    svc.play = _slow_play  # type: ignore[method-assign]

    t0 = time.monotonic()
    details = PlaybackService.enqueue_play(svc, 9, source="manual")
    elapsed = time.monotonic() - t0

    assert details["accepted"] is True
    assert details["playlist_id"] == 9
    assert elapsed < 0.5
    for _ in range(50):
        if entered["n"]:
            break
        time.sleep(0.02)
    assert entered["n"] == 1
    release["go"] = True
    time.sleep(0.05)
