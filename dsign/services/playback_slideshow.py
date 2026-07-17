"""Manual slideshow / mixed playlist loop extracted from PlaylistManager (H-REF PR3)."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .playlist_management import PlaylistManager


class PlaybackSlideshowLoop:
    """Mixed content loop: images by duration, video/audio to EOF, network streams."""

    def __init__(self, pm: "PlaylistManager") -> None:
        self._pm = pm

    def run(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int = 0,
        *,
        first_item_preloaded: bool = False,
        profile_muted: bool = False,
        single_pass: bool = False,
        playback_run_id: Optional[int] = None,
    ):
        """
        Manual playback loop that enforces per-item durations for images and plays videos to EOF.
        Runs in a background thread; advances images by sleeping for their duration and videos by
        polling mpv properties until EOF.

        ``profile_muted``: playlist overrides mute flag (combined with per-file ``muted``).
        ``playback_run_id``: generation token; Stop bumps it so orphan ytdl waits exit cleanly.
        """
        self._pm.logger.info("Starting manual playback loop", extra={"playlist_id": playlist_id, "items_count": len(items)})

        if not items:
            return
        if playback_run_id is None:
            playback_run_id = int(getattr(self._pm, "_playback_run_id", 0) or 0)
        self._pm._mpv_manager.set_playback_session_active(True)
        try:

            start_index = int(start_index or 0)
            if start_index < 0 or start_index >= len(items):
                start_index = 0

            default_duration = 10
            # Single-item playlists: first `loadfile` is done in play(); skip only that one iteration.
            # Without this flag, skip_load stays true forever and the file never reloads for cycle 2+.
            did_skip_first_preload = False
            loop_cycle = 0
            try:
                net_open_abort = int(
                    (os.getenv("DSIGN_PLAYLIST_NET_OPEN_FAIL_ABORT") or "3").strip()
                )
            except ValueError:
                net_open_abort = 3
            net_open_abort = max(2, min(20, net_open_abort))
            try:
                net_open_cooldown_sec = float(
                    (os.getenv("DSIGN_PLAYLIST_NET_OPEN_FAIL_COOLDOWN_SEC") or "90").strip()
                )
            except ValueError:
                net_open_cooldown_sec = 90.0
            net_open_cooldown_sec = max(15.0, min(600.0, net_open_cooldown_sec))
            while (
                not self._pm._stop_event.is_set()
                and self._pm._active_playlist_id == playlist_id
                and self._pm._is_playback_run_current(int(playback_run_id))
            ):
                loop_cycle += 1
                cycle_network_attempted = 0
                cycle_network_failed = 0
                self._pm.logger.debug(
                    "Playlist loop cycle",
                    extra={"playlist_id": playlist_id, "cycle": loop_cycle, "items": len(items)},
                )
                stall_abort = False
                net_open_cycle_abort = False
                # Iterate cyclically starting from start_index.
                for offset in range(len(items)):
                    item_index = (start_index + offset) % len(items)
                    item = items[item_index]
                    self._pm._set_loop_position(item_index, len(items))
                    if (
                        self._pm._stop_event.is_set()
                        or self._pm._active_playlist_id != playlist_id
                        or not self._pm._is_playback_run_current(int(playback_run_id))
                    ):
                        break

                    path = item["path"]
                    is_video = item["is_video"]
                    is_audio = bool(item.get("is_audio"))
                    media_key = str(item.get("key") or path)
                    self._pm._on_playlist_item_start(
                        playlist_id, item, profile_muted=profile_muted
                    )
                    self._pm.logger.info(
                        "Playlist item starting",
                        extra={
                            "playlist_id": playlist_id,
                            "cycle": loop_cycle,
                            "offset": offset,
                            "media_key": media_key,
                            "is_video": is_video,
                            "is_audio": is_audio,
                            "item_muted": bool(item.get("muted", False)),
                        },
                    )
                    raw_duration = item.get("duration")
                    # Only images use duration. Treat 0/None as "missing" for images.
                    duration = raw_duration if (raw_duration is not None and int(raw_duration) >= 1) else default_duration
                    muted = self._pm._effective_playback_muted(
                        item_muted=bool(item.get("muted", False)),
                        profile_muted=profile_muted,
                    )
                    load_cmd: Optional[List[Any]] = None
                    load_ok = True
                    socket_missing = False
                    is_network_reload = False

                    skip_load = bool(
                        first_item_preloaded
                        and offset == 0
                        and not did_skip_first_preload
                    )
                    if skip_load:
                        did_skip_first_preload = True

                    is_preloaded_network = bool(
                        skip_load
                        and is_video
                        and isinstance(path, str)
                        and path.startswith(("http://", "https://", "ytdl://"))
                    )
                    if is_preloaded_network and load_cmd is None:
                        load_cmd = self._pm._preloaded_load_cmd
                        # play() may have seen quiet ytdl IPC — do not pretend success.
                        load_ok = bool(getattr(self._pm, "_preloaded_load_ipc_ok", True))

                    # Only tune mpv for the first preloaded item; before the next loadfile replace
                    # these extra set_property calls just queue behind a busy IPC socket.
                    if skip_load and not is_preloaded_network:
                        try:
                            self._pm._mpv_manager._send_command(
                                {"command": ["set_property", "loop-file", "no"]},
                                timeout=5.0,
                            )
                        except Exception:
                            pass
                        if is_video:
                            try:
                                self._pm._mpv_manager._send_command(
                                    {"command": ["set_property", "keep-open", "no"]},
                                    timeout=3.0,
                                )
                            except Exception:
                                pass

                    # Load next media file
                    load_started = time.monotonic()
                    # Respect per-media backoff to avoid tight loops on failing streams.
                    try:
                        b = self._pm._media_backoff.get(media_key) or {}
                        next_try = float(b.get("next_try_monotonic") or 0.0)
                    except Exception:
                        next_try = 0.0
                    if next_try and time.monotonic() < next_try:
                        wait_sec = max(0.0, next_try - time.monotonic())
                        self._pm.logger.debug(
                            "Backoff active for media",
                            extra={"media_key": media_key, "wait_sec": round(wait_sec, 2)},
                        )
                        self._pm._stop_event.wait(timeout=min(30.0, wait_sec))
                        if self._pm._stop_event.is_set() or self._pm._active_playlist_id != playlist_id:
                            break
                    if not skip_load:
                        if not self._pm._refresh_item_playback_path(item):
                            self._pm.logger.warning(
                                "Failed to refresh external media for playlist loop",
                                extra={"media_key": str(item.get("key") or ""), "playlist_id": playlist_id},
                            )
                            continue
                        path = item["path"]
                    self._pm.logger.debug(
                        "Playlist item",
                        extra={
                            "playlist_id": playlist_id,
                            "cycle": loop_cycle,
                            "offset": offset,
                            "media_key": media_key,
                            "skip_load": skip_load,
                        },
                    )
                    normalized_headers: Dict[str, str] = {}
                    mpv_per_file_opts: Dict[str, Any] = {}
                    # play() already loadfile'd preloaded items; still need header dict for ytdl lavf reapply.
                    if skip_load and is_video and isinstance(path, str) and path.startswith(
                        ("http://", "https://", "ytdl://")
                    ):
                        normalized_headers = self._pm._sanitize_headers_for_mpv(
                            item.get("http_headers") or {},
                            page_url=item.get("page_url"),
                            stream_url=str(path),
                            provider=item.get("provider"),
                        )
                    if not skip_load:
                        is_network_reload = is_video and isinstance(path, str) and (
                            path.startswith("http://")
                            or path.startswith("https://")
                            or path.startswith("ytdl://")
                        )
                        if is_network_reload:
                            try:
                                self._pm._mpv_manager.set_playback_stream_opening(True)
                            except Exception:
                                pass
                            self._pm._prepare_mpv_network_reload()
                        # External streams: Referer/UA must be set before loadfile (and cleared between items).
                        normalized_headers, mpv_per_file_opts = self._pm._apply_mpv_http_headers(item, stream_url=str(path))
                        self._pm._apply_mpv_ytdl_options(item, stream_url=str(path))
                        if is_network_reload:
                            load_cmd = self._pm._mpv_loadfile_command(
                                str(path),
                                "replace",
                                per_file_opts=mpv_per_file_opts,
                            )
                            load_timeout = self._pm._network_loadfile_timeout_sec(
                                str(path), is_network=is_network_reload
                            )
                            load_timeout = max(5.0, min(180.0, float(load_timeout)))
                            load_resp = self._pm._issue_loadfile(
                                load_cmd,
                                media_key=media_key,
                                timeout=load_timeout,
                            )
                            load_ok = bool(load_resp and load_resp.get("error") == "success")
                        elif is_audio:
                            self._pm._prepare_mpv_audio_before_loadfile()
                            try:
                                audio_opts = self._pm._logo_manager.prepare_audio_playback()
                            except Exception:
                                audio_opts = {"vid": "no", "keep-open": "no"}
                            merged_opts = dict(mpv_per_file_opts or {})
                            merged_opts.update(audio_opts)
                            load_ok = self._pm._safe_loadfile(
                                str(path),
                                media_key=media_key,
                                is_video=False,
                                is_audio=True,
                                per_file_opts=merged_opts,
                                timeout=10.0,
                                wait_vo=False,
                            )
                            if not load_ok:
                                self._pm._logo_manager.restore_after_audio_playback()
                                self._pm._brief_idle_logo_on_skip()
                                continue
                        else:
                            self._pm._prepare_mpv_audio_before_loadfile()
                            load_ok = self._pm._safe_loadfile(
                                str(path),
                                media_key=media_key,
                                is_video=is_video,
                                per_file_opts=mpv_per_file_opts,
                                timeout=10.0,
                            )
                            if not load_ok:
                                self._pm._brief_idle_logo_on_skip()
                                continue
                        socket_missing = not os.path.exists(PlaybackConstants.SOCKET_PATH)
                        if not load_ok and not is_network_reload:
                            self._pm.logger.warning(
                                "MPV loadfile failed",
                                extra={
                                    "path": path,
                                    "mpv_response": load_resp,
                                    "socket_missing": socket_missing,
                                },
                            )
                            self._pm._register_media_failure(
                                media_key,
                                reason="socket_missing" if socket_missing else "loadfile_failed",
                            )
                            if socket_missing:
                                self._pm._stop_event.wait(timeout=5.0)
                            continue
                        if not load_ok and is_network_reload:
                            self._pm.logger.info(
                                "MPV loadfile IPC quiet (network); verifying stream open",
                                extra={
                                    "path": str(path)[:120],
                                    "load_timeout_sec": load_timeout,
                                    "socket_missing": socket_missing,
                                },
                            )

                    if is_video:
                        # Network streams: mpv stays idle-active until open; do not treat idle as EOF.
                        is_network = isinstance(path, str) and (
                            path.startswith("http://")
                            or path.startswith("https://")
                            or path.startswith("ytdl://")
                        )
                        stream_ready = False
                        if is_network:
                            cycle_network_attempted += 1
                            if not self._pm._ensure_network_stream_started(
                                item,
                                str(path),
                                normalized_headers=normalized_headers or None,
                                load_cmd=load_cmd,
                                load_ipc_ok=bool(load_ok),
                                playback_run_id=int(playback_run_id),
                            ):
                                # Stop / superseded run: exit without failure bookkeeping
                                # (that used to persist idle over a fresh Play).
                                if (
                                    self._pm._stop_event.is_set()
                                    or not self._pm._is_playback_run_current(
                                        int(playback_run_id)
                                    )
                                ):
                                    break
                                try:
                                    self._pm._mpv_manager.set_playback_stream_opening(False)
                                except Exception:
                                    pass
                                # Stuck paused ytdl:// must not block the next Play.
                                try:
                                    self._pm._prepare_mpv_for_new_play(lock_wait=1.0)
                                except Exception:
                                    pass
                                self._pm.logger.warning(
                                    "MPV network stream open failed",
                                    extra={
                                        "path": str(path)[:120],
                                        "load_ipc_ok": bool(load_ok),
                                        "media_key": media_key,
                                    },
                                )
                                self._pm._register_media_failure(
                                    media_key,
                                    reason="socket_missing" if socket_missing else "open_failed",
                                )
                                if is_network:
                                    cycle_network_failed += 1
                                    self._pm._record_ytdl_open_failure(
                                        media_key=media_key,
                                        reason="open_failed",
                                    )
                                    streak = int(self._pm._consecutive_ytdl_failures or 0)
                                    if streak >= net_open_abort:
                                        self._pm.logger.warning(
                                            "Aborting playlist after consecutive network open failures",
                                            extra={
                                                "playlist_id": playlist_id,
                                                "consecutive_ytdl_failures": streak,
                                                "abort_threshold": net_open_abort,
                                                "resume_index": item_index,
                                                "last_good_media_key": self._pm._last_good_media_key,
                                            },
                                        )
                                        try:
                                            self._pm._persist_playback_status(
                                                playlist_id=None,
                                                status="idle",
                                                source="idle",
                                                clear_rule=True,
                                            )
                                        except Exception:
                                            pass
                                        self._pm._stop_event.set()
                                        break
                                if socket_missing:
                                    self._pm._stop_event.wait(timeout=5.0)
                                continue
                            stream_ready = True
                            self._pm._apply_post_loadfile_playback_props(
                                muted=muted,
                                item_muted=bool(item.get("muted", False)),
                                profile_muted=profile_muted,
                            )
                            if is_network:
                                self._pm._record_ytdl_open_success()
                                self._pm._set_last_good_playback(
                                    playlist_id,
                                    item_index,
                                    media_key,
                                    len(items),
                                )
                        elif not skip_load:
                            self._pm._apply_post_loadfile_playback_props(
                                muted=muted,
                                item_muted=bool(item.get("muted", False)),
                                profile_muted=profile_muted,
                            )
                        self._pm._schedule_content_cache_prefetch(items, item_index)
                        if not self._pm._wait_mpv_video_end(
                            playlist_id,
                            is_network=is_network,
                            stream_ready=stream_ready,
                            poll_sec=1.0,
                            stream_url=str(path) if is_network else None,
                            provider=str(item.get("provider") or "") or None,
                            media_key=media_key,
                            item=item if is_network else None,
                        ):
                            stall_abort = True
                            break
                        skip_start = self._pm._apply_remote_skip_after_item(item_index, len(items))
                        if skip_start is not None:
                            start_index = skip_start
                            break
                    elif is_audio:
                        try:
                            self._pm._mpv_manager.set_playback_local_audio_active(True)
                        except Exception:
                            pass
                        try:
                            if not self._pm._prepare_local_audio_after_loadfile(
                                muted=muted,
                                skip_load=skip_load,
                                item_muted=bool(item.get("muted", False)),
                                profile_muted=profile_muted,
                            ):
                                if not skip_load:
                                    try:
                                        self._pm._logo_manager.restore_after_audio_playback()
                                    except Exception:
                                        pass
                                    self._pm._brief_idle_logo_on_skip()
                                continue
                            if not self._pm._wait_mpv_video_end(
                                playlist_id,
                                is_network=False,
                                stream_ready=True,
                                poll_sec=2.5,
                                media_key=media_key,
                                is_audio=True,
                            ):
                                stall_abort = True
                                try:
                                    self._pm._logo_manager.restore_after_audio_playback()
                                except Exception:
                                    pass
                                break
                            skip_start = self._pm._apply_remote_skip_after_item(item_index, len(items))
                            if skip_start is not None:
                                start_index = skip_start
                                break
                            try:
                                self._pm._logo_manager.restore_after_audio_playback()
                            except Exception:
                                pass
                        finally:
                            try:
                                self._pm._mpv_manager.set_playback_local_audio_active(False)
                            except Exception:
                                pass
                    else:
                        # For still images, keep the frame open to avoid quick close/reopen churn.
                        # (Videos should not be kept open; they are EOF-driven here.)
                        try:
                            self._pm._mpv_manager._send_command(
                                {"command": ["set_property", "keep-open", "yes"]},
                                timeout=2.0,
                            )
                        except Exception:
                            pass

                        # Deterministic image timer:
                        # wait until MPV loaded the file *and* VO is configured, then schedule switch.
                        #
                        # IMPORTANT:
                        # Historically we started the countdown after mpv confirmed the file was loaded.
                        # That makes the *perceived* duration more stable when IO/decoding is slow, but
                        # it also creates a constant drift vs the DB duration (duration + load time).
                        # Default behavior here is to match DB duration from the moment we initiate loadfile.
                        # Use DSIGN_IMAGE_TIMER_MODE=from_ready to restore the old behavior.
                        loaded = self._pm._wait_for_mpv_loaded_path(path, timeout=15.0)
                        vo_ready = self._pm._wait_for_mpv_vo_configured(timeout=5.0)
                        ready_at = time.monotonic()
                        load_wait_sec = round(ready_at - load_started, 3)

                        dur_sec = max(1, int(duration))
                        timer_mode = os.getenv("DSIGN_IMAGE_TIMER_MODE", "from_load").strip().lower()
                        if timer_mode not in ("from_load", "from_ready"):
                            timer_mode = "from_load"
                        base_t = load_started if timer_mode == "from_load" else ready_at
                        switch_at = base_t + dur_sec
                        self._pm.logger.debug(
                            "Image timer scheduled",
                            extra={
                                "path": path,
                                "duration_sec": dur_sec,
                                "timer_mode": timer_mode,
                                "loaded_confirmed": loaded,
                                "vo_configured": vo_ready,
                                "load_wait_sec": load_wait_sec,
                            },
                        )
                        reached = self._pm._sleep_until(switch_at, step=0.2)
                        skip_start = self._pm._apply_remote_skip_after_item(item_index, len(items))
                        if skip_start is not None:
                            start_index = skip_start
                            break
                        if not reached:
                            if self._pm._stop_event.is_set():
                                break
                            continue
                        if reached:
                            fired_at = time.monotonic()
                            drift_sec = round(fired_at - (base_t + dur_sec), 3)
                            # Positive drift means we switched later than scheduled (thread wake / load spikes).
                            self._pm.logger.debug(
                                "Image timer fired",
                                extra={
                                    "path": path,
                                    "duration_sec": dur_sec,
                                    "timer_mode": timer_mode,
                                    "drift_sec": drift_sec,
                                    "load_wait_sec": load_wait_sec,
                                },
                            )

                if net_open_cycle_abort:
                    continue

                if (
                    cycle_network_attempted > 0
                    and cycle_network_failed >= cycle_network_attempted
                    and not stall_abort
                ):
                    next_start = self._pm._handle_all_network_items_failed_cycle(
                        playlist_id=playlist_id,
                        items_count=len(items),
                    )
                    if next_start is not None:
                        start_index = int(next_start)
                        continue
                    break

                if stall_abort or self._pm._stall_restart_was_requested():
                    self._pm.logger.info(
                        "Slideshow loop paused for mpv stall recovery",
                        extra={
                            "playlist_id": playlist_id,
                            "cycle": loop_cycle,
                            "loop_item_index": self._pm._loop_item_index,
                        },
                    )
                    break

                if single_pass:
                    self._pm.logger.info(
                        "Playback override: single-pass cycle complete",
                        extra={"playlist_id": playlist_id, "cycle": loop_cycle},
                    )
                    break

        finally:
            try:
                self._pm._mpv_manager.set_playback_local_audio_active(False)
            except Exception:
                pass
            try:
                if self._pm._logo_manager.ensure_mpv_video_output():
                    pass
                else:
                    self._pm._sync_settings_volume_to_mpv()
            except Exception:
                pass
            self._pm._mpv_manager.set_playback_session_active(False)
            try:
                self._pm._clear_ghost_playing_after_slideshow_exit(
                    playlist_id,
                    int(playback_run_id),
                )
            except Exception:
                pass
