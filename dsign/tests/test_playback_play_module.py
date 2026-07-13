"""Unit tests for playback_play module (H-REF PR4 extract)."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_play import PlaybackPlayRunner


def test_play_impl_delegates_to_play_runner(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._playback_play.run = MagicMock(return_value=True)
    assert pm._play_impl(3, start_index=1, source="schedule", rule_id=9) is True
    pm._playback_play.run.assert_called_once_with(
        3,
        start_index=1,
        preserve_stall_tracking=False,
        single_pass=False,
        source="schedule",
        rule_id=9,
    )


def test_play_runner_missing_playlist_raises(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    db = MagicMock()
    db.query.return_value.get.return_value = None
    pm = PlaylistManager(null_logger, None, str(tmp_path), db, MagicMock(), MagicMock())
    runner = PlaybackPlayRunner(pm)
    try:
        runner.run(999)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Failed to start playback" in str(e)


def test_playback_play_imports_thread():
    """Regression: H-REF extract used Thread without importing it."""
    from dsign.services import playback_play
    from threading import Thread

    assert playback_play.Thread is Thread
