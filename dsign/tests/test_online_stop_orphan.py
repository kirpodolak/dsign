"""Online ytdl open must not leave a zombie that kills later Play (offline included)."""

from __future__ import annotations

from threading import Event, Thread
from unittest.mock import MagicMock

from dsign.services.playback_network import PlaybackNetworkHelper
from dsign.services.playlist_management import PlaylistManager


def test_stop_play_thread_bumps_run_id_and_tracks_orphan(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    release = Event()

    def _worker():
        # Ignore stop_event — simulates stuck ytdl IPC until release.
        release.wait(timeout=5.0)

    thr = Thread(target=_worker, name="stuck-ytdl", daemon=True)
    pm._play_thread = thr
    thr.start()
    run0 = int(pm._playback_run_id)

    pm._stop_play_thread(join_timeout=0.2)

    assert int(pm._playback_run_id) == run0 + 1
    assert pm._play_thread is None
    assert len(pm._orphan_play_threads) == 1
    assert thr.is_alive()
    # New Play must not see a sticky stop bit.
    assert not pm._stop_event.is_set()

    release.set()
    thr.join(timeout=2.0)


def test_ensure_does_not_reissue_loadfile_when_run_stale(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    helper = PlaybackNetworkHelper(pm)
    pm._issue_ytdl_loadfile = MagicMock()
    helper._wait_mpv_ytdl_stream_opening = MagicMock(return_value=False)
    helper._ytdl_stream_open_progress = MagicMock(return_value=None)

    run_id = int(pm._playback_run_id)
    pm._bump_playback_run_id()  # Stop / newer play

    ok = helper._ensure_network_stream_started_impl(
        {"key": "ext-1", "provider": "rutube"},
        "ytdl://https://rutube.ru/video/x",
        "ext-1",
        load_cmd=["loadfile", "ytdl://https://rutube.ru/video/x", "replace"],
        load_ipc_ok=True,
        playback_run_id=run_id,
    )
    assert ok is False
    pm._issue_ytdl_loadfile.assert_not_called()


def test_ensure_does_not_reissue_loadfile_when_stop_set(null_logger, tmp_path):
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), MagicMock(), MagicMock())
    helper = PlaybackNetworkHelper(pm)
    pm._issue_ytdl_loadfile = MagicMock()
    helper._wait_mpv_ytdl_stream_opening = MagicMock(return_value=False)
    pm._stop_event.set()

    ok = helper._ensure_network_stream_started_impl(
        {"key": "ext-2"},
        "ytdl://https://vk.com/video1",
        "ext-2",
        load_cmd=["loadfile", "ytdl://https://vk.com/video1", "replace"],
        load_ipc_ok=False,
        playback_run_id=int(pm._playback_run_id),
    )
    assert ok is False
    pm._issue_ytdl_loadfile.assert_not_called()


def test_playback_stop_api_uses_enqueue_stop():
    from dsign.routes.api import api_routes

    # Ensure the route source prefers enqueue_stop (async) over sync stop.
    import inspect

    src = inspect.getsource(api_routes)
    assert "enqueue_stop" in src
    assert 'bucket="playback_stop"' in src
