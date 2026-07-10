"""EOF / end-of-item wait loop extracted from PlaylistManager (H-REF PR1)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .playback_constants import PlaybackConstants

if TYPE_CHECKING:
    from .playlist_management import PlaylistManager

def midstream_reload_max_attempts() -> int:
    try:
        n = int((os.getenv("DSIGN_MPV_MIDSTREAM_RELOAD_MAX") or "1").strip())
    except ValueError:
        n = 1
    return max(0, min(5, n))


def midstream_ipc_advance_enabled() -> bool:
    raw = (os.getenv("DSIGN_MPV_MIDSTREAM_IPC_ADVANCE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def should_advance_after_midstream_ipc_failure(
    *,
    is_network: bool,
    eof_capable: bool,
    time_pos: Optional[float],
    duration: Optional[float],
) -> bool:
    """IPC died mid-stream with meaningful duration left — advance instead of restart."""
    if not midstream_ipc_advance_enabled():
        return False
    if not (is_network and eof_capable):
        return False
    if network_stream_near_eof(time_pos=time_pos, duration=duration):
        return False
    if time_pos is not None and time_pos >= 30.0:
        return True
    return duration is not None and duration > 120.0


def is_external_stream_provider(
    *, provider: Optional[str] = None, stream_url: Optional[str] = None
) -> bool:
    """Rutube / VK Video and other ytdl-backed external streams prone to HLS EOF hangs."""
    prov = str(provider or "").strip().lower()
    if prov in ("rutube", "vkvideo"):
        return True
    path_s = str(stream_url or "").strip().lower()
    if path_s.startswith("ytdl://"):
        return True
    if "rutube.ru" in path_s or ("river-" in path_s and "rutube" in path_s):
        return True
    if "vkvideo.ru" in path_s or "vk.com/video" in path_s or "okcdn" in path_s:
        return True
    return False


def float_env(name: str, default: float, *, lo: float, hi: float) -> float:
    try:
        v = float((os.getenv(name) or str(default)).strip())
    except ValueError:
        v = float(default)
    return max(lo, min(hi, v))


def network_eof_advance_sec() -> float:
    """Finish Rutube/VK HLS slightly before reported duration to avoid demuxer hang."""
    return float_env("DSIGN_MPV_NETWORK_EOF_ADVANCE_SEC", 8.0, lo=0.0, hi=60.0)


def network_near_eof_stagnation_sec() -> float:
    """When near duration, treat frozen time-pos as EOF quickly (not mpv restart)."""
    return float_env(
        "DSIGN_MPV_NETWORK_NEAR_EOF_STAGNATION_SEC", 15.0, lo=5.0, hi=120.0
    )


def network_stream_near_eof(
    *, time_pos: Optional[float], duration: Optional[float]
) -> bool:
    if (
        time_pos is None
        or duration is None
        or duration <= 0.5
        or time_pos < 0.0
    ):
        return False
    return time_pos + network_eof_advance_sec() >= duration


def proactive_refresh_interval_minutes_raw() -> str:
    return (
        os.getenv("DSIGN_MPV_PROACTIVE_REFRESH_INTERVAL_MIN")
        or os.getenv("DSIGN_MPV_PROACTIVE_REFRESH_MIN")
        or "25"
    ).strip()


def proactive_refresh_enabled() -> bool:
    return proactive_refresh_interval_minutes_raw().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )


def proactive_refresh_interval_sec() -> Optional[float]:
    """Recurring mid-roll reload interval (default 25 min). None when disabled."""
    if not proactive_refresh_enabled():
        return None
    try:
        minutes = float(proactive_refresh_interval_minutes_raw())
    except ValueError:
        minutes = 25.0
    minutes = max(5.0, min(120.0, minutes))
    return minutes * 60.0


def proactive_refresh_min_duration_sec() -> float:
    """Optional floor: skip proactive refresh on short rolls. 0 = any length."""
    return float_env(
        "DSIGN_MPV_PROACTIVE_REFRESH_MIN_DURATION_SEC", 0.0, lo=0.0, hi=7200.0
    )


def tail_mpv_log_segments(*, limit: int = 5) -> List[str]:
    log_path = Path(
        os.getenv("DSIGN_MPV_LOG_FILE") or "/var/lib/dsign/mpv/mpv.log"
    )
    if not log_path.is_file():
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    hits: List[str] = []
    for line in reversed(lines):
        if "segment-" in line or "Opening" in line:
            hits.append(line.strip()[-240:])
        if len(hits) >= limit:
            break
    return list(reversed(hits))


def log_ipc_dead_diagnostics(
    logger,
    *,
    playlist_id: int,
    media_key: Optional[str],
    time_pos: Optional[float],
    duration: Optional[float],
    stall_polls: int,
) -> None:
    logger.warning(
        "playlist_eof_stall: IPC dead context snapshot",
        extra={
            "playlist_id": playlist_id,
            "media_key": media_key,
            "time_pos": time_pos,
            "duration": duration,
            "stall_polls": stall_polls,
            "mpv_log_segments": tail_mpv_log_segments(limit=5),
        },
    )


class PlaybackEofWaiter:
    """Waits for mpv to finish the current video/audio item (EOF detection)."""

    def __init__(self, pm: "PlaylistManager") -> None:
        self._pm = pm

    def wait_video_end(
        self,
        playlist_id: int,
        *,
        is_network: bool,
        stream_ready: bool,
        poll_sec: float = 1.0,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
        media_key: Optional[str] = None,
        item: Optional[Dict[str, Any]] = None,
        is_audio: bool = False,
    ) -> bool:
        """
        End-of-playback detection that works when `eof-reached` is permanently unavailable.

        Returns True when the item ended normally; False when playback should stop
        without advancing (stall restart, user stop, or playlist change).
        """
        start = time.time()
        # Short grace caused false EOF on HLS (idle flicker while buffering) → tight loadfile loops
        # and IPC lock starvation (UI/API hang). Use a longer window for network playback.
        try:
            nw_grace = float((os.getenv("DSIGN_MPV_NETWORK_IDLE_GRACE_SEC") or "25").strip())
        except ValueError:
            nw_grace = 25.0
        nw_grace = max(3.0, min(120.0, nw_grace))
        if is_network:
            grace_until = time.monotonic() + nw_grace
        elif is_audio:
            grace_until = time.monotonic() + 2.0
        else:
            grace_until = time.monotonic() + 0.3
        consecutive_idle = 0
        if is_network:
            try:
                self._pm._mpv_manager.set_playback_network_active(True)
            except Exception:
                pass
        if is_network:
            try:
                poll_sec = float(
                    (os.getenv("DSIGN_MPV_NETWORK_EOF_POLL_SEC") or "3.0").strip()
                )
            except ValueError:
                poll_sec = 2.0
            poll_sec = max(1.0, min(10.0, poll_sec))

        consecutive_ipc_stall = 0
        default_stall = "5" if is_network else "15"
        try:
            stall_limit = int(
                (os.getenv("DSIGN_MPV_EOF_IPC_STALL_POLLS") or default_stall).strip()
            )
        except ValueError:
            stall_limit = int(default_stall)
        stall_limit = max(3, min(120, stall_limit))

        try:
            stagnation_sec = float(
                (os.getenv("DSIGN_MPV_PLAYBACK_STAGNATION_SEC") or "90").strip()
            )
        except ValueError:
            stagnation_sec = 90.0
        stagnation_sec = max(20.0, min(600.0, stagnation_sec))
        try:
            ipc_dead_sec = float(
                (os.getenv("DSIGN_MPV_EOF_IPC_DEAD_SEC") or "45").strip()
            )
        except ValueError:
            ipc_dead_sec = 45.0
        ipc_dead_sec = max(15.0, min(300.0, ipc_dead_sec))

        last_time_pos: Optional[float] = None
        last_duration: Optional[float] = None
        last_time_pos_change = time.monotonic()
        last_ipc_ok = time.monotonic()
        poll_tick = 0
        local_idle_confirm = 2 if is_audio else 1
        playback_started = False
        eof_capable = is_network and is_external_stream_provider(
            provider=provider, stream_url=stream_url
        )
        eof_advance_sec = network_eof_advance_sec() if eof_capable else 0.0
        near_eof_stagnation_sec = (
            network_near_eof_stagnation_sec() if eof_capable else stagnation_sec
        )
        midstream_reload_attempts = 0
        proactive_refresh_next_at: Optional[float] = None
        use_eof_events = False
        if is_network:
            try:
                self._pm._mpv_manager.enable_playback_eof_events()
                use_eof_events = bool(self._pm._mpv_manager._playback_eof_events_enabled)
                if use_eof_events:
                    drained = self._pm._mpv_manager.drain_playback_events("end-file")
                    if drained:
                        self._pm.logger.debug(
                            "Drained stale mpv end-file events before EOF wait",
                            extra={
                                "playlist_id": playlist_id,
                                "media_key": media_key,
                                "drained": drained,
                            },
                        )
            except Exception:
                use_eof_events = False

        def _finish_video_item(reason: str) -> None:
            if media_key:
                self._pm._clear_stall_count_for_media(media_key)
            self._pm.logger.info(
                "Playlist item finished",
                extra={
                    "playlist_id": playlist_id,
                    "is_network": is_network,
                    "media_key": media_key,
                    "reason": reason,
                },
            )

        while not self._pm._stop_event.is_set() and self._pm._active_playlist_id == playlist_id:
            if self._pm._item_skip_event.is_set():
                break
            if time.time() - start > 6 * 3600:
                break

            poll_wait = max(0.2, float(poll_sec))
            if use_eof_events and time.monotonic() >= grace_until:
                ev = self._pm._mpv_manager.wait_playback_event(
                    "end-file", timeout=poll_wait
                )
                if ev is not None:
                    ev_reason = str(ev.get("reason") or "").strip().lower()
                    # loadfile replace emits end-file/stop for the *previous* file — not EOF.
                    if ev_reason == "eof":
                        _finish_video_item("mpv_end_file_eof")
                        break
                    if ev_reason in ("stop", "quit"):
                        self._pm._mpv_manager.drain_playback_events("end-file")

            snap_timeout = 10.0 if is_network else 4.0
            try:
                raw_to = (os.getenv("DSIGN_MPV_EOF_SNAPSHOT_TIMEOUT_SEC") or "").strip()
                if raw_to:
                    snap_timeout = float(raw_to)
            except ValueError:
                pass
            snap_timeout = max(2.0, min(20.0, snap_timeout))

            poll_tick += 1
            tp_raw = self._pm._mpv_get_light("time-pos", timeout=snap_timeout)
            tp = self._pm._snap_number({"time-pos": tp_raw}, "time-pos")
            if is_audio and tp is not None and tp > 0.05:
                playback_started = True

            idle_raw: Optional[Any] = None
            if is_network:
                if poll_tick % 2 == 0:
                    idle_raw = self._pm._mpv_get_light("idle-active", timeout=snap_timeout)
            else:
                idle_raw = self._pm._mpv_get_light("idle-active", timeout=snap_timeout)

            dur: Optional[float] = None
            poll_duration = (not is_network) or eof_capable
            if poll_duration and poll_tick % 3 == 0:
                dur_raw = self._pm._mpv_get_light("duration", timeout=snap_timeout)
                dur = self._pm._snap_number({"duration": dur_raw}, "duration")
            if dur is not None and dur > 0.5:
                last_duration = dur

            min_refresh_duration = proactive_refresh_min_duration_sec()
            duration_long_enough = min_refresh_duration <= 0 or (
                (last_duration is not None and last_duration >= min_refresh_duration)
                or (tp is not None and tp >= min_refresh_duration)
            )
            refresh_interval_sec = proactive_refresh_interval_sec()
            if (
                eof_capable
                and item is not None
                and duration_long_enough
                and tp is not None
                and refresh_interval_sec is not None
            ):
                if proactive_refresh_next_at is None:
                    proactive_refresh_next_at = refresh_interval_sec
                if tp >= proactive_refresh_next_at:
                    refresh_pos = float(tp)
                    if self._pm._try_midstream_network_reload(
                        item,
                        playlist_id=playlist_id,
                        reason="proactive_refresh",
                        seek_to=refresh_pos,
                    ):
                        self._pm._mpv_manager.drain_playback_events("end-file")
                        last_time_pos = refresh_pos
                        last_time_pos_change = time.monotonic()
                        consecutive_ipc_stall = 0
                        last_ipc_ok = time.monotonic()
                        proactive_refresh_next_at = refresh_pos + refresh_interval_sec
                        self._pm.logger.info(
                            "Proactive refresh completed; next scheduled",
                            extra={
                                "playlist_id": playlist_id,
                                "media_key": media_key,
                                "seek_to": refresh_pos,
                                "next_refresh_at_sec": proactive_refresh_next_at,
                                "interval_sec": refresh_interval_sec,
                            },
                        )
                        continue

            got_ipc = tp is not None or idle_raw is not None or dur is not None
            socket_missing = not os.path.exists(PlaybackConstants.SOCKET_PATH)
            if got_ipc:
                last_ipc_ok = time.monotonic()
                consecutive_ipc_stall = 0
            else:
                consecutive_ipc_stall += 1
                ipc_dead = (
                    consecutive_ipc_stall >= stall_limit
                    or time.monotonic() - last_ipc_ok >= ipc_dead_sec
                )
                if ipc_dead:
                    near_eof_ipc = eof_capable and network_stream_near_eof(
                        time_pos=last_time_pos, duration=last_duration
                    )
                    if near_eof_ipc:
                        self._pm.logger.info(
                            "playlist_eof: IPC quiet near network stream end; finishing item",
                            extra={
                                "playlist_id": playlist_id,
                                "provider": provider,
                                "time_pos": last_time_pos,
                                "duration": last_duration,
                                "stall_polls": consecutive_ipc_stall,
                            },
                        )
                        _finish_video_item("network_near_eof_ipc_dead")
                        break
                    midstream_advance = should_advance_after_midstream_ipc_failure(
                        is_network=is_network,
                        eof_capable=eof_capable,
                        time_pos=last_time_pos,
                        duration=last_duration,
                    )
                    if socket_missing:
                        if midstream_advance:
                            self._pm.logger.warning(
                                "playlist_eof_stall: MPV socket missing mid-stream;"
                                " finishing item and advancing",
                                extra={
                                    "playlist_id": playlist_id,
                                    "is_network": is_network,
                                    "media_key": media_key,
                                    "time_pos": last_time_pos,
                                    "duration": last_duration,
                                    "stall_polls": consecutive_ipc_stall,
                                },
                            )
                            _finish_video_item("network_midstream_socket_missing")
                            self._pm._set_stall_recovery_advance()
                            self._pm._request_mpv_stall_restart(
                                playlist_id=playlist_id,
                                reason="socket_missing_midstream",
                                media_key=media_key,
                                skip_stall_count=True,
                            )
                            return False
                        self._pm.logger.warning(
                            "playlist_eof_stall: MPV socket missing during EOF wait",
                            extra={
                                "playlist_id": playlist_id,
                                "is_network": is_network,
                                "stall_polls": consecutive_ipc_stall,
                            },
                        )
                        self._pm._request_mpv_stall_restart(
                            playlist_id=playlist_id,
                            reason="socket_missing_eof",
                            media_key=media_key,
                        )
                        return False
                    if midstream_advance:
                        self._pm.logger.warning(
                            "playlist_eof_stall: MPV IPC dead mid-stream;"
                            " finishing item and advancing",
                            extra={
                                "playlist_id": playlist_id,
                                "is_network": is_network,
                                "media_key": media_key,
                                "time_pos": last_time_pos,
                                "duration": last_duration,
                                "stall_polls": consecutive_ipc_stall,
                            },
                        )
                        _finish_video_item("network_midstream_ipc_dead")
                        self._pm._set_stall_recovery_advance()
                        self._pm._request_mpv_stall_restart(
                            playlist_id=playlist_id,
                            reason="ipc_dead_midstream",
                            media_key=media_key,
                            skip_stall_count=True,
                        )
                        return False
                    log_ipc_dead_diagnostics(self._pm.logger, 
                        playlist_id=playlist_id,
                        media_key=media_key,
                        time_pos=last_time_pos,
                        duration=last_duration,
                        stall_polls=consecutive_ipc_stall,
                    )
                    self._pm.logger.warning(
                        "playlist_eof_stall: MPV IPC dead during EOF wait; requesting restart",
                        extra={
                            "playlist_id": playlist_id,
                            "is_network": is_network,
                            "media_key": media_key,
                            "stall_polls": consecutive_ipc_stall,
                        },
                    )
                    self._pm._request_mpv_stall_restart(
                        playlist_id=playlist_id,
                        reason="ipc_dead_eof",
                        media_key=media_key,
                    )
                    return False

            if dur is not None and dur > 0.5 and tp is not None:
                end_margin = eof_advance_sec if eof_capable else 0.2
                if tp + end_margin >= dur:
                    reason = (
                        "network_duration_reached"
                        if eof_capable
                        else "duration_reached"
                    )
                    _finish_video_item(reason)
                    break

            if tp is not None and time.monotonic() >= grace_until:
                if last_time_pos is None or abs(tp - last_time_pos) > 0.05:
                    last_time_pos = tp
                    last_time_pos_change = time.monotonic()
                else:
                    near_end = network_stream_near_eof(
                        time_pos=tp, duration=dur
                    ) or (
                        dur is not None
                        and dur > 0.5
                        and tp + 2.0 >= dur
                    )
                    stall_limit_sec = (
                        near_eof_stagnation_sec if near_end else stagnation_sec
                    )
                    if time.monotonic() - last_time_pos_change >= stall_limit_sec:
                        if near_end:
                            self._pm.logger.info(
                                "playlist_eof: network stream near end with frozen time-pos; finishing item",
                                extra={
                                    "playlist_id": playlist_id,
                                    "is_network": is_network,
                                    "provider": provider,
                                    "time_pos": tp,
                                    "duration": dur,
                                    "stagnation_sec": round(
                                        time.monotonic() - last_time_pos_change, 1
                                    ),
                                },
                            )
                            _finish_video_item("network_near_eof_stagnation")
                            break
                        self._pm.logger.warning(
                            "playlist_eof_stall: playback time-pos frozen"
                            + (
                                "; requesting restart"
                                if is_network
                                else "; treating as end"
                            ),
                            extra={
                                "playlist_id": playlist_id,
                                "is_network": is_network,
                                "time_pos": tp,
                                "duration": dur,
                                "stagnation_sec": round(
                                    time.monotonic() - last_time_pos_change, 1
                                ),
                            },
                        )
                        if is_network:
                            max_reload = midstream_reload_max_attempts()
                            if (
                                eof_capable
                                and item is not None
                                and midstream_reload_attempts < max_reload
                            ):
                                midstream_reload_attempts += 1
                                if self._pm._try_midstream_network_reload(
                                    item,
                                    playlist_id=playlist_id,
                                    reason="time_pos_stagnation",
                                    seek_to=tp,
                                ):
                                    self._pm._mpv_manager.drain_playback_events(
                                        "end-file"
                                    )
                                    last_time_pos = None
                                    last_time_pos_change = time.monotonic()
                                    consecutive_ipc_stall = 0
                                    last_ipc_ok = time.monotonic()
                                    continue
                            self._pm._request_mpv_stall_restart(
                                playlist_id=playlist_id,
                                reason="time_pos_stagnation",
                                media_key=media_key,
                            )
                            return False
                        _finish_video_item("time_pos_stagnation")
                        break

            idle = self._pm._snap_bool({"idle-active": idle_raw}, "idle-active")
            if idle is True and time.monotonic() >= grace_until:
                if is_network:
                    if stream_ready:
                        consecutive_idle += 1
                        if consecutive_idle >= 2:
                            _finish_video_item("network_idle")
                            break
                else:
                    consecutive_idle += 1
                    if consecutive_idle >= local_idle_confirm:
                        if is_audio and not playback_started:
                            consecutive_idle = 0
                        else:
                            _finish_video_item("local_idle")
                            break
            elif idle is False:
                consecutive_idle = 0

            if not use_eof_events or time.monotonic() < grace_until:
                self._pm._stop_event.wait(timeout=poll_wait)
        if is_network:
            try:
                self._pm._mpv_manager.set_playback_network_active(False)
            except Exception:
                pass
        if self._pm._stall_restart_was_requested():
            return False
        return (
            not self._pm._stop_event.is_set()
            and self._pm._active_playlist_id == playlist_id
        )
