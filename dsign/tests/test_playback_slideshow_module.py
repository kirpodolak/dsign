"""Unit tests for playback_slideshow module (H-REF PR3 extract)."""

from __future__ import annotations

from unittest.mock import MagicMock

from dsign.services.playback_slideshow import PlaybackSlideshowLoop


def test_slideshow_run_no_items_is_noop(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    loop = PlaybackSlideshowLoop(pm)
    loop.run(1, [], 0)
    pm._mpv_manager.set_playback_session_active.assert_not_called()


def test_playlist_manager_delegates_manual_slideshow_loop(null_logger, tmp_path):
    from dsign.services.playlist_management import PlaylistManager

    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    pm._playback_slideshow.run = MagicMock()
    pm._manual_slideshow_loop(7, [{"path": "a.jpg", "is_video": False}], 0)
    pm._playback_slideshow.run.assert_called_once_with(
        7,
        [{"path": "a.jpg", "is_video": False}],
        0,
        first_item_preloaded=False,
        profile_muted=False,
        single_pass=False,
        playback_run_id=None,
    )
