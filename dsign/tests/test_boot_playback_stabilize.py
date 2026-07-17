"""Boot must not race ScheduleEngine / configure into flash-then-idle."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dsign.services.playlist_management import PlaylistManager
from dsign.services.schedule_engine import ScheduleEngine


def test_schedule_engine_start_defers_first_evaluate():
    playback = MagicMock()
    schedule = MagicMock()
    engine = ScheduleEngine(playback, schedule, logger=MagicMock())
    engine.evaluate_and_apply = MagicMock()
    engine.start()
    try:
        engine.evaluate_and_apply.assert_not_called()
        assert engine._timer is not None
        assert engine._running is True
    finally:
        engine.stop()


def test_boot_grace_blocks_desync_window(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    assert pm.in_boot_grace() is False
    pm.begin_boot_grace(30.0)
    assert pm.in_boot_grace() is True


def test_configure_skips_idle_when_schedule_engine_attached():
    from dsign import _configure_playback_service

    app = MagicMock()
    app.app_context.return_value = MagicMock(
        __enter__=MagicMock(return_value=None),
        __exit__=MagicMock(return_value=False),
    )
    playback = MagicMock()
    playback._schedule_engine = MagicMock()
    app.playback_service = playback
    app.logger = MagicMock()

    with patch("dsign._run_idle_logo_attempts") as idle, patch(
        "dsign._resume_playback_now"
    ) as resume, patch("dsign.extensions.db") as db:
        row = MagicMock()
        row.playlist_id = None
        db.session.query.return_value.first.return_value = row
        # Drive the thread body synchronously.
        with patch("dsign.Thread") as Thr:
            def _run(target=None, **_k):
                assert target is not None
                target()
                return MagicMock()

            Thr.side_effect = _run
            _configure_playback_service(app)

        idle.assert_not_called()
        resume.assert_not_called()
        app.logger.info.assert_any_call(
            "ScheduleEngine attached — skipping configure idle-logo/resume "
            "(boot resume owns restore)"
        )
