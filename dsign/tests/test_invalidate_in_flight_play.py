"""Stop/return invalidate in-flight play; enqueue_play only marks play starting."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_service import PlaybackService
from dsign.services.playlist_management import PlaylistManager


def test_invalidate_in_flight_play_bumps_seq_and_sets_stop(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    seq0 = pm._begin_play_seq()
    pm.invalidate_in_flight_play()
    assert not pm._is_play_seq_current(seq0)
    assert pm._stop_event.is_set()


def test_enqueue_play_marks_starting_not_invalidate():
    svc = PlaybackService.__new__(PlaybackService)
    svc.logger = MagicMock()
    svc._app = None
    svc._log_error = MagicMock()
    svc._log_warning = MagicMock()
    svc._last_desync_recover_ts = 0.0
    svc._playlist_manager = MagicMock()
    order = []

    def _mark():
        order.append("mark")

    def _claim(*_a, **_k):
        order.append("claim")

    svc._playlist_manager.mark_play_starting.side_effect = _mark
    svc._playlist_manager.claim_playback_intent.side_effect = _claim
    svc.play = MagicMock(return_value=True)

    details = PlaybackService.enqueue_play(svc, 9, source="manual")
    assert details["accepted"] is True
    assert order == ["mark", "claim"]
    svc._playlist_manager.invalidate_in_flight_play.assert_not_called()


def test_return_to_schedule_invalidates_before_plan():
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
    svc._playlist_manager = MagicMock()
    order = []

    def _inv():
        order.append("invalidate")

    svc._playlist_manager.invalidate_in_flight_play.side_effect = _inv
    svc._playlist_manager._mpv_has_active_media.return_value = False

    row = MagicMock()
    row.source = "manual"
    row.status = "playing"
    row.rule_id = 1
    session = MagicMock()
    session.query.return_value.get.return_value = row
    svc.db_session = MagicMock()
    svc.db_session.session = session

    def _plan(**_k):
        order.append("plan")
        return ("play", 3, 2)

    svc._schedule_engine = MagicMock()
    svc._schedule_engine.plan_action.side_effect = _plan
    svc.enqueue_play = MagicMock(return_value={"accepted": True})

    assert PlaybackService.return_to_schedule(svc) is True
    assert order == ["invalidate", "plan"]
    svc.enqueue_play.assert_called_once()
