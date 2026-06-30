import os
import re
import subprocess
import traceback
import time
from contextlib import nullcontext
from threading import Event, Lock, Thread
from typing import Callable, Dict, List, Optional, Any
from pathlib import Path

from .playback_constants import PlaybackConstants

class PlaylistManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager, logo_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        # `db_session` is inconsistently passed around the codebase:
        # sometimes it's a Flask-SQLAlchemy `db` object, sometimes it's `db.session`.
        # Normalize to a real Session that has `.query()/.add()/.commit()/.rollback()`.
        self.db_session = getattr(db_session, 'session', db_session)
        self._mpv_manager = mpv_manager
        self._logo_manager = logo_manager
        self._last_playback_state = {}
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)

        self._play_thread: Optional[Thread] = None
        self._stop_event = Event()
        self._active_playlist_id: Optional[int] = None
        # External media resolver is optional; attached lazily to avoid tight coupling.
        self._external_media_service = None
        self._content_cache = None
        self._settings_service = None
        # Backoff per media key for unstable/blocked streams to avoid busy looping loadfile.
        # key -> {failures:int, next_try_monotonic:float}
        self._media_backoff: Dict[str, Dict[str, Any]] = {}
        self._app = None
        self._preloaded_stream_ready = False
        self._preloaded_load_cmd: Optional[List[Any]] = None
        self._current_media_label: Optional[str] = None
        self._current_media_lock = Lock()
        self._loop_item_index: Optional[int] = None
        self._loop_items_count: int = 0
        self._loop_position_lock = Lock()
        self._stall_restart_lock = Lock()
        self._stall_restart_pending = False
        self._stall_count_lock = Lock()
        self._stall_count_by_media: Dict[str, int] = {}
        self._stall_recovery_advance = False
        self._app_ready = Event()
        self._slideshow_crash_callback: Optional[Callable[[], None]] = None
        self._last_loaded_media_key: Optional[str] = None
        self._load_in_progress = False
        self._load_lock = Lock()
        self._post_mpv_restart_until: float = 0.0
        self._consecutive_ytdl_failures: int = 0
        self._ytdl_health_lock = Lock()
        self._last_good_item_index: Optional[int] = None
        self._last_good_media_key: Optional[str] = None
        self._last_good_items_count: int = 0
        self._last_good_playlist_id: Optional[int] = None
        self._status_snapshot_cache: Dict[str, Any] = {}
        self._status_snapshot_ts: float = 0.0
        self._playback_mute_lock = Lock()
        self._playback_item_muted = False
        self._playback_profile_muted = False
        self._audio_route_applied_for_play = False

    def set_slideshow_crash_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._slideshow_crash_callback = callback

    def set_app(self, app) -> None:
        """Attach Flask app so background playback threads can use db.session safely."""
        self._app = app
        self._app_ready.set()

    def _ensure_app_wired(self, timeout: float = 90.0) -> bool:
        if self._app is not None:
            return True
        return self._app_ready.wait(timeout=max(0.0, float(timeout)))

    def _app_context(self):
        if self._app is None:
            self._ensure_app_wired(timeout=90.0)
        return self._app.app_context() if self._app is not None else nullcontext()

    def set_external_media_service(self, service) -> None:
        """Attach external media resolver service (optional)."""
        self._external_media_service = service

    def set_content_cache(self, service) -> None:
        """Attach disk cache for external media (C1, optional)."""
        self._content_cache = service

    def set_settings_service(self, service) -> None:
        """Attach settings service so playback can mirror volume/audio-route into MPV."""
        self._settings_service = service
        try:
            self._logo_manager.set_audio_resync_callback(self._sync_settings_audio_to_mpv)
        except Exception:
            pass

    def _sync_settings_audio_route_to_mpv(self, *, cycle_ao: bool = False) -> bool:
        """Apply ao/audio-device from settings (expensive — can interrupt ALSA; call sparingly)."""
        svc = self._settings_service
        if not svc:
            self.logger.warning(
                "MPV audio route skipped: settings service unavailable",
                extra={"event": "audio_route_skip"},
            )
            return False
        try:
            st = svc.load_settings()
            updates = svc.build_mpv_audio_updates(settings=st)
        except Exception as exc:
            self.logger.warning(
                "MPV audio route resolution failed",
                extra={"event": "audio_route_error", "error": str(exc)},
            )
            return False
        if not updates:
            self.logger.warning(
                "MPV audio route: nothing to apply",
                extra={"event": "audio_route_empty"},
            )
            return False
        mpv_cfg = st.get("mpv") if isinstance(st.get("mpv"), dict) else {}
        route = mpv_cfg.get("audio-route", "auto")
        try:
            self._mpv_manager.rebind_audio_output(
                str(updates.get("ao") or "alsa"),
                str(updates.get("audio-device") or ""),
                cycle_ao=cycle_ao,
            )
            self.logger.warning(
                "MPV audio route applied",
                extra={
                    "event": "audio_route_applied",
                    "route": str(route),
                    "ao": updates.get("ao"),
                    "audio_device": updates.get("audio-device"),
                    "cycle_ao": cycle_ao,
                },
            )
            return True
        except Exception as exc:
            self.logger.warning(
                "MPV audio route IPC failed",
                extra={"event": "audio_route_ipc_error", "error": str(exc)},
            )
            return False

    def _alsa_dev_index_from_mpv_device(self, adev: Optional[str]) -> Optional[str]:
        s = str(adev or "")
        if "DEV=" not in s:
            return None
        dev = s.rsplit("DEV=", 1)[-1].strip()
        return dev if dev.isdigit() else None

    def _kick_alsa_hardware_after_demuxer(self) -> None:
        """Re-unmute IEC958 for the active DEV and nudge mpv audio-device after the stream opens."""
        adev = self._resolved_audio_device_for_loadfile()
        svc = self._settings_service
        if svc:
            try:
                svc.unmute_pch_digital_outputs(
                    dev=self._alsa_dev_index_from_mpv_device(adev)
                )
            except Exception:
                pass
        if adev:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "audio-device", adev]},
                    timeout=2.0,
                    max_attempts=1,
                )
            except Exception:
                pass

    def _alsa_pcm_status_hint(self) -> Optional[str]:
        try:
            card = 0
            if self._settings_service:
                idx = self._settings_service._pch_card_index()
                if idx is not None:
                    card = idx
            root = Path(f"/proc/asound/card{card}")
            if not root.is_dir():
                return None
            parts: list[str] = []
            for st in sorted(root.glob("pcm*p/sub0/status")):
                pcm = st.parent.parent.name
                txt = st.read_text(encoding="utf-8", errors="ignore").strip()
                state = "unknown"
                for line in txt.splitlines():
                    line = line.strip()
                    if line.startswith("state:"):
                        state = line.split(":", 1)[1].strip()
                        break
                parts.append(f"{pcm}:{state}")
                if state in ("RUNNING", "PREPARED"):
                    hp_path = st.parent / "hw_params"
                    try:
                        hp = hp_path.read_text(encoding="utf-8", errors="ignore").strip()
                        if hp and hp != "closed":
                            rate_m = re.search(r"rate:\s*(\d+)", hp)
                            fmt_m = re.search(r"format:\s*(\S+)", hp)
                            if rate_m or fmt_m:
                                parts.append(
                                    f"{pcm}_hw={fmt_m.group(1) if fmt_m else '?'}"
                                    f"@{rate_m.group(1) if rate_m else '?'}Hz"
                                )
                    except Exception:
                        pass
            return ";".join(parts) if parts else None
        except Exception:
            return None

    def _alsa_pcm_has_running_playback(self) -> bool:
        hint = (self._alsa_pcm_status_hint() or "").upper()
        return ":RUNNING" in hint or "STATE: RUNNING" in hint

    def _ensure_mpv_alsa_pcm_open(self) -> None:
        """If mpv decodes audio but ALSA PCM is closed, force ao to open the sink."""
        if self._alsa_pcm_has_running_playback():
            return
        try:
            codec = self._mpv_manager.get_property_light("audio-codec-name", timeout=1.0)
            tp = self._mpv_manager.get_property_light("time-pos", timeout=1.0)
        except Exception:
            codec, tp = None, None
        if not codec:
            return
        try:
            tp_f = float(tp) if tp is not None else 0.0
        except (TypeError, ValueError):
            tp_f = 0.0
        if tp_f <= 0.0 and tp is None:
            return
        adev = self._resolved_audio_device_for_loadfile() or ""
        self.logger.warning(
            "ALSA PCM closed while mpv decodes audio; forcing ao reopen",
            extra={
                "event": "alsa_pcm_force_open",
                "audio_codec": str(codec),
                "time_pos": tp,
                "alsa_pcm_status": self._alsa_pcm_status_hint(),
                "audio_device": adev,
            },
        )
        try:
            self._mpv_manager.force_alsa_ao_open("alsa", adev)
        except Exception as exc:
            self.logger.warning(
                "ALSA PCM force open failed",
                extra={"event": "alsa_pcm_force_open_fail", "error": str(exc)},
            )
            return
        self._kick_alsa_hardware_after_demuxer()
        self._sync_settings_volume_to_mpv()

    def _prepare_mpv_audio_before_loadfile(self) -> None:
        """Unmute ALSA for the upcoming loadfile; device binding is in loadfile options."""
        if not self._audio_route_applied_for_play:
            self._kick_alsa_hardware_after_demuxer()
            self._audio_route_applied_for_play = True
        self._sync_settings_volume_to_mpv()

    def _resolved_audio_device_for_loadfile(self) -> Optional[str]:
        """ALSA device string to pass into mpv loadfile per-file options."""
        svc = self._settings_service
        if not svc:
            return None
        try:
            updates = svc.build_mpv_audio_updates(settings=svc.load_settings())
            adev = str(updates.get("audio-device") or "").strip()
            if adev and adev.lower() != "auto":
                return adev
        except Exception:
            pass
        return None

    def _augment_per_file_audio_opts(
        self, per_file_opts: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        opts = dict(per_file_opts or {})
        adev = self._resolved_audio_device_for_loadfile()
        if adev:
            opts["audio-device"] = adev
        return opts

    def _log_mpv_audio_state(self, *, event: str, settings_volume: Optional[float] = None) -> None:
        """Best-effort readback for field diagnostics."""
        try:
            props = ("audio-device", "mute", "volume", "ao", "pause", "time-pos", "audio-codec-name", "aid")
            snap: Dict[str, Any] = {}
            for prop in props:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", prop]},
                    timeout=1.5,
                    max_attempts=1,
                )
                snap[prop] = (resp or {}).get("data")
            extra: Dict[str, Any] = {"event": event, **snap}
            if settings_volume is not None:
                extra["settings_volume"] = settings_volume
            pcm = self._alsa_pcm_status_hint()
            if pcm:
                extra["alsa_pcm_status"] = pcm
            self.logger.warning("MPV audio state", extra=extra)
        except Exception:
            pass

    def _sync_settings_volume_to_mpv(self) -> None:
        """Re-apply master volume from settings.json (safe after each loadfile)."""
        svc = self._settings_service
        if not svc:
            return
        try:
            st = svc.load_settings()
            vol_raw = st.get("volume")
            if vol_raw is None:
                return
            v = float(max(0, min(100, int(vol_raw))))
            self._mpv_manager._send_command(
                {"command": ["set_property", "volume", v]},
                timeout=2.0,
                max_attempts=1,
            )
            self._log_mpv_audio_state(event="volume_synced", settings_volume=v)
        except Exception:
            pass

    def _sync_settings_audio_to_mpv(self) -> None:
        """
        Apply audio-route + volume from settings.json to MPV.

        On Pi/direct HDMI the dashboard often adjusts ALSA only while audible level is mpv's
        ``volume`` property — leaving mpv at 0 after loadfile otherwise.
        """
        self._sync_settings_audio_route_to_mpv()
        self._sync_settings_volume_to_mpv()

    def _effective_playback_muted(self, *, item_muted: bool, profile_muted: bool) -> bool:
        """Global UI mute OR playlist-profile mute OR per-file mute."""
        g = False
        try:
            if self._settings_service:
                st = self._settings_service.load_settings()
                g = bool(st.get("mute", False))
        except Exception:
            pass
        return g or bool(profile_muted) or bool(item_muted)

    def _set_playback_mute_context(self, *, item_muted: bool, profile_muted: bool) -> None:
        with self._playback_mute_lock:
            self._playback_item_muted = bool(item_muted)
            self._playback_profile_muted = bool(profile_muted)

    def reapply_effective_mute_to_mpv(self) -> None:
        """Re-apply playlist/profile/global mute after volume API may have cleared mpv mute."""
        with self._playback_mute_lock:
            item_muted = self._playback_item_muted
            profile_muted = self._playback_profile_muted
        muted = self._effective_playback_muted(
            item_muted=item_muted,
            profile_muted=profile_muted,
        )
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "mute", "yes" if muted else "no"]},
                timeout=2.0,
                max_attempts=1,
            )
        except Exception:
            pass

    def _media_label_for_file_name(self, file_name: str) -> str:
        """Human-readable label for a playlist file entry (local name or external title)."""
        name = str(file_name or "").strip()
        if not name:
            return ""
        if name.startswith("ext-"):
            svc = self._external_media_service
            if svc:
                row = svc.get_by_key(name)
                title = str(getattr(row, "title", "") or "").strip() if row else ""
                if title:
                    return title
            return name
        return os.path.basename(name)

    def _item_media_label(self, item: Dict[str, Any]) -> str:
        label = str(item.get("label") or "").strip()
        if label:
            return label
        key = str(item.get("key") or "").strip()
        if key:
            return self._media_label_for_file_name(key)
        path = str(item.get("path") or "").strip()
        if path:
            return os.path.basename(path)
        return ""

    def _set_current_media_label(self, label: Optional[str]) -> None:
        cleaned = str(label or "").strip() or None
        with self._current_media_lock:
            self._current_media_label = cleaned

    def _get_current_media_label(self) -> Optional[str]:
        with self._current_media_lock:
            return self._current_media_label

    def _publish_current_media(self, playlist_id: int, item: Dict[str, Any]) -> None:
        label = self._item_media_label(item)
        with self._current_media_lock:
            if label == (self._current_media_label or ""):
                return
            self._current_media_label = label or None
        try:
            if self.socketio:
                self.socketio.emit(
                    "playback_update",
                    {
                        "status": "playing",
                        "playlist_id": playlist_id,
                        "current_media": label or None,
                    },
                )
        except Exception:
            pass

    def _set_loop_position(self, index: int, items_count: int) -> None:
        with self._loop_position_lock:
            self._loop_item_index = int(index)
            self._loop_items_count = max(0, int(items_count))

    def _clear_loop_position(self) -> None:
        with self._loop_position_lock:
            self._loop_item_index = None
            self._loop_items_count = 0

    def get_resume_start_index(self, *, advance: bool = True) -> int:
        """Index to start/resume playlist loop; advance=True skips the interrupted item."""
        with self._loop_position_lock:
            idx = self._loop_item_index
            count = self._loop_items_count
        if idx is None or count <= 0:
            return 0
        if advance:
            return (int(idx) + 1) % count
        return int(idx)

    def get_resume_start_index_for_hung_recovery(self) -> int:
        """After hung mpv restart, prefer last successfully opened item."""
        with self._loop_position_lock:
            good_idx = self._last_good_item_index
            good_count = self._last_good_items_count
            loop_idx = self._loop_item_index
            loop_count = self._loop_items_count
        if good_idx is not None and good_count > 0:
            return int(good_idx) % good_count
        if loop_idx is not None and loop_count > 0:
            return int(loop_idx)
        return 0

    @staticmethod
    def _is_network_stream_path(path: Any) -> bool:
        p = str(path or "")
        return p.startswith(("http://", "https://", "ytdl://"))

    def _is_local_video_item(self, item: Dict[str, Any]) -> bool:
        return bool(item.get("is_video")) and not self._is_network_stream_path(item.get("path"))

    def _playlist_playback_mode(self, items: List[Dict[str, Any]]) -> str:
        """Target branching: all-local-video → mpv playlist; single → loop-file=inf; else manual."""
        if not items:
            return "manual"
        if not all(self._is_local_video_item(i) for i in items):
            return "manual"
        if len(items) == 1:
            return "local_single"
        return "local_playlist"

    def _mpv_set_local_playback_props(
        self,
        *,
        loop_file: str,
        loop_playlist: bool,
        prefetch: bool,
    ) -> None:
        props = (
            ("loop-file", loop_file),
            ("loop-playlist", "yes" if loop_playlist else "no"),
            ("prefetch-playlist", "yes" if prefetch else "no"),
            ("keep-open", "no"),
        )
        for prop, val in props:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", prop, val]},
                    timeout=3.0,
                )
            except Exception:
                pass

    def _write_local_video_m3u(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int,
    ) -> Path:
        start_index = int(start_index or 0) % len(items)
        lines = ["#EXTM3U\n"]
        included = 0
        for offset in range(len(items)):
            idx = (start_index + offset) % len(items)
            path_str = str(items[idx]["path"])
            ok, reason = self._validate_local_media_path(path_str, is_video=True)
            if not ok:
                self.logger.warning(
                    "M3U: skip invalid local video",
                    extra={
                        "playlist_id": playlist_id,
                        "media_key": items[idx].get("key"),
                        "path": path_str,
                        "reason": reason,
                    },
                )
                continue
            path = Path(path_str).resolve()
            lines.append(f"{path}\n")
            included += 1
        if included == 0:
            raise ValueError(f"Playlist {playlist_id}: no valid local video files for M3U")
        dest = self.tmp_dir / f"local-playlist-{playlist_id}.m3u"
        dest.write_text("".join(lines), encoding="utf-8")
        return dest

    def _apply_item_mute_property(self, item: Dict[str, Any], *, profile_muted: bool) -> None:
        item_muted = bool(item.get("muted", False))
        self._set_playback_mute_context(item_muted=item_muted, profile_muted=profile_muted)
        muted = self._effective_playback_muted(
            item_muted=item_muted,
            profile_muted=profile_muted,
        )
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "mute", "yes" if muted else "no"]},
                timeout=3.0,
            )
        except Exception:
            pass

    def _set_last_good_playback(
        self,
        playlist_id: int,
        item_index: int,
        media_key: str,
        items_count: int,
    ) -> None:
        with self._loop_position_lock:
            self._last_good_playlist_id = int(playlist_id)
            self._last_good_item_index = int(item_index)
            self._last_good_items_count = max(1, int(items_count))
            self._last_good_media_key = str(media_key or "")

    def _record_ytdl_open_success(self) -> None:
        with self._ytdl_health_lock:
            prev = int(self._consecutive_ytdl_failures or 0)
            self._consecutive_ytdl_failures = 0
        if prev > 0:
            self.logger.info(
                "ytdl open recovered after failure streak",
                extra={"previous_consecutive_ytdl_failures": prev},
            )

    def _record_ytdl_open_failure(self, *, media_key: str = "", reason: str = "") -> None:
        with self._ytdl_health_lock:
            self._consecutive_ytdl_failures = int(self._consecutive_ytdl_failures or 0) + 1
            streak = self._consecutive_ytdl_failures
        self.logger.warning(
            "ytdl/network stream open failed (streak)",
            extra={
                "consecutive_ytdl_failures": streak,
                "media_key": media_key or None,
                "reason": reason or None,
                "cdn_may_be_down": streak >= 3,
            },
        )

    def get_network_playback_health(self) -> Dict[str, Any]:
        with self._ytdl_health_lock:
            streak = int(self._consecutive_ytdl_failures or 0)
        with self._loop_position_lock:
            return {
                "consecutive_ytdl_failures": streak,
                "cdn_may_be_down": streak >= 3,
                "last_good_media_key": self._last_good_media_key,
                "last_good_item_index": self._last_good_item_index,
                "last_good_playlist_id": self._last_good_playlist_id,
                "post_mpv_restart_window": self._in_post_mpv_restart_window(),
            }

    def _clear_current_media_label(self, *, emit: bool = True, playlist_id: Optional[int] = None) -> None:
        with self._current_media_lock:
            self._current_media_label = None
        if not emit or not self.socketio:
            return
        try:
            payload: Dict[str, Any] = {"current_media": None}
            if playlist_id is not None:
                payload["playlist_id"] = playlist_id
            self.socketio.emit("playback_update", payload)
        except Exception:
            pass

    @staticmethod
    def _classify_local_media_suffix(ext: str) -> tuple[bool, bool]:
        """Return (is_video, is_audio) for a local file suffix."""
        suffix = str(ext or "").lower()
        is_video = suffix in PlaybackConstants.VIDEO_EXTENSIONS
        is_audio = suffix in PlaybackConstants.AUDIO_EXTENSIONS
        return is_video, is_audio

    def _resolve_playlist_item_path(self, file_name: str) -> Optional[Dict[str, Any]]:
        """
        Convert a playlist file_name into a playback dict: {path,is_video,is_audio,duration,muted}.
        Supports both local filenames and synthetic external keys ext-<id>.
        """
        if not file_name:
            return None

        # External media key: ext-<id>
        if str(file_name).startswith("ext-"):
            svc = self._external_media_service
            if not svc:
                # If service isn't attached, fallback to the raw key (will fail to loadfile).
                return {"path": str(file_name), "is_video": True}
            row = svc.get_by_key(str(file_name))
            if not row:
                return None
            page_url = str(getattr(row, "url", "") or "")
            provider = str(getattr(row, "provider", "") or "")
            cache = self._content_cache
            if cache is not None:
                cached = cache.build_playback_dict(
                    str(file_name),
                    page_url=page_url,
                    provider=provider,
                )
                if cached is not None:
                    return cached
            # Always re-resolve on play: CDN URLs are signed to yt-dlp's egress; cached URLs break on the Pi.
            pb = svc.ensure_fresh_playback(row, max_age_sec=0)
            return {
                "key": str(file_name),
                "path": pb.get("url") or row.resolved_url or row.url,
                "is_video": True,
                "http_headers": pb.get("http_headers") or {},
                "page_url": page_url,
                "provider": provider,
            }

        # Local file
        file_path = self.upload_folder / str(file_name)
        if not file_path.exists():
            return None
        ext = file_path.suffix.lower()
        is_video, is_audio = self._classify_local_media_suffix(ext)
        return {"path": str(file_path), "is_video": is_video, "is_audio": is_audio}

    def _refresh_item_playback_path(self, item: Dict[str, Any]) -> bool:
        """
        Re-resolve external media before each loop iteration.

        ``play()`` builds ``items[]`` once; VK/Rutube CDN signatures and ytdl state must be
        refreshed on every cycle, not only on the first ``play()`` call.
        """
        key = item.get("key")
        if not key or not str(key).startswith("ext-"):
            return True
        resolved = self._resolve_playlist_item_path(str(key))
        if not resolved or not resolved.get("path"):
            return False
        prev = str(item.get("path") or "")
        item["path"] = resolved["path"]
        item["http_headers"] = resolved.get("http_headers") or {}
        item["page_url"] = resolved.get("page_url")
        item["provider"] = resolved.get("provider")
        new_path = str(item.get("path") or "")
        if new_path != prev:
            self.logger.debug(
                "Refreshed external media URL for playlist loop",
                extra={"media_key": str(key), "path_preview": new_path[:120]},
            )
        return True

    def _schedule_content_cache_prefetch(
        self,
        items: List[Dict[str, Any]],
        current_index: int,
    ) -> None:
        cache = self._content_cache
        if cache is None or not cache.prefetch_enabled() or not items:
            return
        n = len(items)
        if n < 1:
            return
        next_index = (int(current_index) + 1) % n
        next_item = items[next_index]
        media_key = str(next_item.get("key") or "")
        if not media_key.startswith("ext-"):
            return
        page_url = str(next_item.get("page_url") or "")
        provider = str(next_item.get("provider") or "")
        if not page_url and self._external_media_service:
            row = self._external_media_service.get_by_key(media_key)
            if row:
                page_url = str(getattr(row, "url", "") or "")
                provider = provider or str(getattr(row, "provider", "") or "")
        if not page_url:
            return
        cache.prefetch_async(media_key=media_key, page_url=page_url, provider=provider)

    def _ensure_network_stream_started(
        self,
        item: Dict[str, Any],
        stream_url: str,
        *,
        normalized_headers: Optional[Dict[str, str]] = None,
        load_cmd: Optional[List[Any]] = None,
        load_ipc_ok: bool = True,
    ) -> bool:
        """Wait until mpv opens a network stream (background thread only)."""
        path_s = str(stream_url or "")
        media_key = str(item.get("key") or path_s)
        if not path_s.startswith(("http://", "https://", "ytdl://")):
            return True

        headers = normalized_headers
        if not headers:
            headers = self._sanitize_headers_for_mpv(
                item.get("http_headers") or {},
                page_url=item.get("page_url"),
                stream_url=path_s,
                provider=item.get("provider"),
            )

        try:
            self._mpv_manager.set_playback_stream_opening(True)
        except Exception:
            pass
        try:
            return self._ensure_network_stream_started_impl(
                item,
                path_s,
                media_key,
                headers=headers,
                load_cmd=load_cmd,
                load_ipc_ok=load_ipc_ok,
            )
        finally:
            try:
                self._mpv_manager.set_playback_stream_opening(False)
            except Exception:
                pass

    def mark_post_mpv_restart(self, within_sec: float = 300.0) -> None:
        """Shorter ytdl open waits right after hung/systemd mpv recovery."""
        self._post_mpv_restart_until = time.monotonic() + max(30.0, float(within_sec))

    def _in_post_mpv_restart_window(self) -> bool:
        return time.monotonic() < float(self._post_mpv_restart_until or 0.0)

    def _ytdl_open_timeout_sec(self, default_sec: float) -> float:
        streak = max(0, int(self._consecutive_ytdl_failures or 0))
        capped = float(default_sec)
        if streak >= 2:
            capped = min(capped, 45.0)
        elif streak >= 1:
            capped = min(capped, 90.0)
        if self._in_post_mpv_restart_window():
            try:
                recover_cap = float(
                    (os.getenv("DSIGN_MPV_YTDL_OPEN_SEC_AFTER_RECOVER") or "60").strip()
                )
            except ValueError:
                recover_cap = 60.0
            capped = min(capped, recover_cap)
        return max(15.0, capped)

    def _all_network_fail_cooldown_sec(self) -> float:
        try:
            sec = float(
                (os.getenv("DSIGN_PLAYLIST_ALL_NETWORK_FAIL_COOLDOWN_SEC") or "300").strip()
            )
        except ValueError:
            sec = 300.0
        return max(60.0, min(1800.0, sec))

    def _handle_all_network_items_failed_cycle(
        self,
        *,
        playlist_id: int,
        items_count: int,
    ) -> Optional[int]:
        """
        Full playlist cycle had only network open failures.

        Returns start_index for the next outer loop iteration, or None if unchanged.
        """
        health = self.get_network_playback_health()
        self.logger.error(
            "playlist: all network items failed in cycle",
            extra={
                "playlist_id": playlist_id,
                "items_count": items_count,
                **health,
            },
        )
        good_idx = self._last_good_item_index
        if good_idx is not None and self._last_good_items_count > 0:
            self.logger.warning(
                "playlist: retrying last-good media after full network failure cycle",
                extra={
                    "playlist_id": playlist_id,
                    "last_good_media_key": self._last_good_media_key,
                    "resume_index": int(good_idx),
                },
            )
            return int(good_idx) % self._last_good_items_count
        cooldown = self._all_network_fail_cooldown_sec()
        try:
            self._logo_manager.display_idle_logo()
        except Exception:
            pass
        self.logger.warning(
            "playlist: no last-good media; logo + cooldown before next cycle",
            extra={
                "playlist_id": playlist_id,
                "cooldown_sec": round(cooldown, 1),
                "consecutive_ytdl_failures": health.get("consecutive_ytdl_failures"),
            },
        )
        self._stop_event.wait(timeout=cooldown)
        return 0

    def _issue_loadfile(
        self,
        load_cmd: List[Any],
        *,
        media_key: str,
        force: bool = False,
        timeout: float = 5.0,
        max_attempts: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Centralized loadfile IPC with deduplication by media_key.

        Use ``media_key`` (ext-4, ytdl:// URL) — not resolved HLS path after ytdl_hook.
        """
        key = str(media_key or "").strip()
        if not key and isinstance(load_cmd, list) and len(load_cmd) >= 2:
            key = str(load_cmd[1])

        with self._load_lock:
            if not force and self._load_in_progress and self._last_loaded_media_key == key:
                self.logger.warning(
                    "loadfile dedup: skipped duplicate while load in progress",
                    extra={"media_key": key, "event": "loadfile_dedup"},
                )
                return None
            self._load_in_progress = True
            self._last_loaded_media_key = key

        try:
            return self._mpv_manager._send_command(
                {"command": load_cmd},
                timeout=timeout,
                max_attempts=max_attempts,
            )
        finally:
            with self._load_lock:
                self._load_in_progress = False

    def _probe_local_video_file(self, file_path: Path) -> bool:
        """ffprobe -v error (A3): reject corrupt/unreadable local video before loadfile."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-i", str(file_path)],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _validate_local_media_path(
        self, path: str, *, is_video: bool, is_audio: bool = False
    ) -> tuple[bool, str]:
        if self._is_network_stream_path(path):
            return True, "network"
        fp = Path(path)
        if not fp.is_file():
            return False, "missing"
        if (is_video or is_audio) and not self._probe_local_video_file(fp):
            return False, "ffprobe"
        return True, "ok"

    def _wait_vo_configured(self, timeout_sec: float = 5.0) -> bool:
        deadline = time.monotonic() + float(timeout_sec)
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            vo = self._mpv_manager.get_property_light("vo-configured", timeout=1.0)
            if vo is True:
                return True
            self._stop_event.wait(0.2)
        return False

    def _brief_idle_logo_on_skip(self) -> None:
        try:
            self._logo_manager.display_idle_logo()
        except Exception:
            pass

    def _safe_loadfile(
        self,
        path: str,
        *,
        media_key: str,
        is_video: bool,
        is_audio: bool = False,
        mode: str = "replace",
        per_file_opts: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
        wait_vo: bool = True,
    ) -> bool:
        """
        A3: exists + ffprobe (local video/audio) → loadfile → wait vo-configured.
        Network/ytdl paths skip ffprobe; caller handles stream-open separately.
        """
        path_s = str(path or "")
        is_network = self._is_network_stream_path(path_s)
        if not is_network:
            ok, reason = self._validate_local_media_path(
                path_s, is_video=is_video, is_audio=is_audio
            )
            if not ok:
                self.logger.warning(
                    "safe_loadfile: invalid local media, skipping",
                    extra={"path": path_s, "media_key": media_key, "reason": reason},
                )
                return False

        load_cmd = self._mpv_loadfile_command(path_s, mode, per_file_opts=per_file_opts)
        load_timeout = (
            self._network_loadfile_timeout_sec(path_s, is_network=True)
            if is_network
            else float(timeout)
        )
        load_resp = self._issue_loadfile(
            load_cmd,
            media_key=media_key,
            timeout=load_timeout,
            max_attempts=1 if is_network else None,
        )
        if not load_resp or load_resp.get("error") != "success":
            self.logger.warning(
                "safe_loadfile: loadfile IPC failed",
                extra={"path": path_s[:200], "media_key": media_key, "mpv_response": load_resp},
            )
            return False

        if wait_vo and is_video and not is_network:
            if not self._wait_vo_configured(5.0):
                self.logger.warning(
                    "safe_loadfile: vo-configured timeout",
                    extra={"path": path_s[:200], "media_key": media_key},
                )
                return False
        return True

    def _issue_ytdl_loadfile(self, load_cmd: List[Any], *, media_key: str) -> None:
        """Fire loadfile; mpv may not IPC-reply until ytdl_hook finishes."""
        try:
            self._issue_loadfile(
                load_cmd,
                media_key=media_key,
                force=True,
                timeout=self._ytdl_loadfile_ipc_timeout_sec(),
                max_attempts=1,
            )
        except Exception:
            pass

    @staticmethod
    def _ytdl_loadfile_ipc_timeout_sec() -> float:
        try:
            sec = float((os.getenv("DSIGN_MPV_YTDL_LOADFILE_IPC_SEC") or "12").strip())
        except ValueError:
            sec = 12.0
        return max(5.0, min(30.0, sec))

    def _ytdl_stream_open_progress(self) -> Optional[str]:
        """True progress signal for ytdl:// resolution (not idle-active alone)."""
        soc_raw = self._mpv_get_light("stream-open-filename", timeout=0.35)
        soc = self._snap_str({"stream-open-filename": soc_raw}, "stream-open-filename")
        if soc and not str(soc).startswith("ytdl://") and len(str(soc).strip()) > 8:
            return "stream-open-filename"
        pth_raw = self._mpv_get_light("path", timeout=0.35)
        pth = self._snap_str({"path": pth_raw}, "path")
        if pth and not str(pth).startswith("ytdl://") and len(str(pth).strip()) > 8:
            return "path"
        dct_raw = self._mpv_get_light("demuxer-cache-time", timeout=0.35)
        dct = self._snap_number({"demuxer-cache-time": dct_raw}, "demuxer-cache-time")
        if dct is not None and dct > 0.05:
            return "demuxer-cache-time"
        dem_raw = self._mpv_get_light("demuxer", timeout=0.35)
        dem = self._snap_str({"demuxer": dem_raw}, "demuxer")
        if dem and str(dem).strip():
            tp_raw = self._mpv_get_light("time-pos", timeout=0.35)
            if self._snap_number({"time-pos": tp_raw}, "time-pos") is not None:
                return "demuxer+time-pos"
        return None

    def _wait_mpv_ytdl_stream_opening(self, *, timeout_sec: float = 180.0) -> bool:
        """Poll until ytdl_hook resolves to a real stream URL or playback starts."""
        try:
            timeout_sec = float(
                (os.getenv("DSIGN_MPV_YTDL_OPEN_SEC") or str(timeout_sec)).strip()
            )
        except ValueError:
            pass
        deadline = time.monotonic() + max(30.0, min(600.0, float(timeout_sec)))
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            reason = self._ytdl_stream_open_progress()
            if reason:
                self.logger.info(
                    "ytdl stream opening progress",
                    extra={"reason": reason},
                )
                return True
            self._stop_event.wait(timeout=0.75)
        return False

    def _ensure_network_stream_started_impl(
        self,
        item: Dict[str, Any],
        path_s: str,
        media_key: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        load_cmd: Optional[List[Any]] = None,
        load_ipc_ok: bool = True,
    ) -> bool:
        is_ytdl = path_s.startswith("ytdl://")
        if is_ytdl:
            if load_cmd is not None and not load_ipc_ok:
                if not self._wait_mpv_ytdl_stream_opening(timeout_sec=20.0):
                    self.logger.info(
                        "ytdl: re-issuing loadfile after IPC quiet",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    self._issue_ytdl_loadfile(load_cmd, media_key=media_key)
            open_sec = self._ytdl_open_timeout_sec(180.0)
            if not self._wait_mpv_ytdl_stream_opening(timeout_sec=open_sec):
                if load_cmd is not None:
                    self.logger.info(
                        "ytdl: retrying loadfile after open wait timed out",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    self._issue_ytdl_loadfile(load_cmd, media_key=media_key)
                    retry_sec = self._ytdl_open_timeout_sec(90.0)
                    if not self._wait_mpv_ytdl_stream_opening(timeout_sec=retry_sec):
                        self.logger.warning(
                            "ytdl stream did not open after loadfile retries",
                            extra={"media_key": media_key, "path_preview": path_s[:120]},
                        )
                        return False
                else:
                    self.logger.warning(
                        "ytdl stream did not open",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    return False
            try:
                self._apply_mpv_lavf_headers_after_ytdl_hook(
                    normalized_headers=headers or {},
                    stream_url=path_s,
                    provider=str(item.get("provider") or "") if item.get("provider") else None,
                    timeout_sec=12.0,
                    skip_idle_wait=True,
                )
            except Exception:
                pass
            if self._mpv_get_light("time-pos", timeout=2.0) is not None:
                pass
            elif not self._wait_mpv_network_demuxer_ready(timeout_sec=30.0, poll_sec=0.5):
                if self._stop_event.is_set():
                    return False
                if self._ytdl_stream_open_progress() is None:
                    self.logger.warning(
                        "ytdl demuxer not ready after stream URL resolved",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    self._log_mpv_network_debug_snapshot(media_key=media_key, url=path_s)
                    return False
        else:
            leave_to = 90.0
            if not self._wait_mpv_leave_idle(
                timeout_sec=leave_to, poll_sec=0.6, snap_timeout=8.0
            ):
                if self._stop_event.is_set():
                    return False
                self.logger.warning(
                    "Network stream did not leave idle after play() loadfile",
                    extra={"media_key": media_key, "path_preview": path_s[:120]},
                )
                return False

            try:
                dem_to = float((os.getenv("DSIGN_MPV_DEMUXER_WAIT_SEC") or "45").strip())
            except ValueError:
                dem_to = 45.0
            dem_to = max(5.0, min(120.0, dem_to))
            if not self._wait_mpv_network_demuxer_ready(timeout_sec=dem_to, poll_sec=0.4):
                if self._stop_event.is_set():
                    return False
                self.logger.warning(
                    "Network stream demuxer not ready after play() loadfile",
                    extra={"media_key": media_key, "path_preview": path_s[:120]},
                )
                self._log_mpv_network_debug_snapshot(media_key=media_key, url=path_s)
                return False

        if self._detect_mpv_instant_eof(window_sec=2.5):
            self.logger.warning(
                "Network stream instant EOF after play() loadfile",
                extra={"media_key": media_key, "path_preview": path_s[:120]},
            )
            return False

        self.logger.info(
            "Network stream ready after play() loadfile",
            extra={"media_key": media_key, "path_preview": path_s[:120]},
        )
        self._reset_media_backoff(media_key)
        try:
            self._mpv_manager._reset_playback_ipc_fail_streak()
        except Exception:
            pass
        return True

    def _wait_for_mpv_loaded_path(self, expected_path: str, timeout: float = 10.0) -> bool:
        """
        Wait until MPV reports the expected `path` as loaded.

        We intentionally start the "virtual timer" after MPV has actually switched to the file,
        otherwise slow IO/decoding makes per-item durations feel random.
        """
        if not expected_path:
            return False

        deadline = time.monotonic() + max(0.1, float(timeout))
        last_seen = None
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            last_seen = self._mpv_get_light("path", timeout=2.0)
            if last_seen == expected_path:
                return True
            # small poll interval; keep it low but not busy-loop
            time.sleep(0.1)

        self.logger.debug(
            "MPV did not confirm loaded path within timeout",
            extra={"expected_path": expected_path, "last_seen_path": last_seen, "timeout_sec": timeout},
        )
        return False

    def _wait_for_mpv_vo_configured(self, timeout: float = 5.0) -> bool:
        """
        Wait until MPV reports `vo-configured=true`.

        On DRM/KMS, `path` may switch before the frame is actually presented. Waiting for
        vo-configured helps reduce visible flicker / "blink" between images.
        """
        deadline = time.monotonic() + max(0.1, float(timeout))
        last_val = None
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            last_val = self._mpv_get_light("vo-configured", timeout=2.0)
            if last_val is True:
                return True
            time.sleep(0.1)
        self.logger.debug(
            "MPV vo-configured did not become true within timeout",
            extra={"timeout_sec": timeout, "last_value": last_val},
        )
        return False

    def _sleep_until(self, deadline_monotonic: float, step: float = 0.2) -> bool:
        """Sleep until deadline or stop event; returns True if reached deadline."""
        step = max(0.05, float(step))
        while True:
            if self._stop_event.is_set():
                return False
            now = time.monotonic()
            remaining = deadline_monotonic - now
            if remaining <= 0:
                return True
            self._stop_event.wait(timeout=min(step, remaining))

    def _mpv_snapshot(self, props: List[str], *, timeout: float = 3.0) -> Dict[str, Optional[Any]]:
        """Batched property read-through (persistent IPC phase 2)."""
        try:
            return self._mpv_manager.get_properties_snapshot(props, timeout=timeout)
        except Exception:
            return {p: None for p in props}

    def _mpv_get_light(self, prop: str, *, timeout: float = 8.0) -> Optional[Any]:
        """Single-property IPC read for playback polling (no batch retries)."""
        try:
            return self._mpv_manager.get_property_light(prop, timeout=timeout)
        except Exception:
            return None

    def _mark_stall_restart_pending(self) -> None:
        with self._stall_restart_lock:
            self._stall_restart_pending = True

    def _stall_restart_was_requested(self) -> bool:
        with self._stall_restart_lock:
            return bool(self._stall_restart_pending)

    def _clear_stall_restart_pending(self) -> None:
        with self._stall_restart_lock:
            self._stall_restart_pending = False

    def _stall_advance_threshold(self) -> int:
        try:
            n = int((os.getenv("DSIGN_MPV_STALL_ADVANCE_AFTER") or "2").strip())
        except ValueError:
            n = 2
        return max(1, min(10, n))

    def _midstream_reload_max_attempts(self) -> int:
        try:
            n = int((os.getenv("DSIGN_MPV_MIDSTREAM_RELOAD_MAX") or "1").strip())
        except ValueError:
            n = 1
        return max(0, min(5, n))

    def _record_stall_for_media(self, media_key: str) -> tuple[int, bool]:
        """Return (stall_count, should_advance_to_next_item_on_recovery)."""
        key = str(media_key or "").strip()
        if not key:
            return 0, False
        threshold = self._stall_advance_threshold()
        with self._stall_count_lock:
            count = int(self._stall_count_by_media.get(key, 0)) + 1
            self._stall_count_by_media[key] = count
            advance = count >= threshold
            if advance:
                self._stall_recovery_advance = True
            return count, advance

    def _clear_stall_count_for_media(self, media_key: str) -> None:
        key = str(media_key or "").strip()
        if not key:
            return
        with self._stall_count_lock:
            self._stall_count_by_media.pop(key, None)

    def _reset_stall_tracking(self) -> None:
        with self._stall_count_lock:
            self._stall_count_by_media.clear()
            self._stall_recovery_advance = False

    def consume_stall_recovery_advance(self) -> bool:
        """True once after N stalls on the same item — recovery should skip to next index."""
        with self._stall_count_lock:
            advance = bool(self._stall_recovery_advance)
            self._stall_recovery_advance = False
            if advance:
                self._stall_count_by_media.clear()
            return advance

    def _set_stall_recovery_advance(self) -> None:
        """Next mpv recovery should resume at the following playlist index."""
        with self._stall_count_lock:
            self._stall_recovery_advance = True

    def _midstream_ipc_advance_enabled(self) -> bool:
        raw = (os.getenv("DSIGN_MPV_MIDSTREAM_IPC_ADVANCE") or "1").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _should_advance_after_midstream_ipc_failure(
        self,
        *,
        is_network: bool,
        eof_capable: bool,
        time_pos: Optional[float],
        duration: Optional[float],
    ) -> bool:
        """
        IPC died while the stream still has meaningful duration left — skip to next item
        instead of restarting the same long HLS roll from the beginning.
        """
        if not self._midstream_ipc_advance_enabled():
            return False
        if not (is_network and eof_capable):
            return False
        if self._network_stream_near_eof(time_pos=time_pos, duration=duration):
            return False
        if time_pos is not None and time_pos >= 30.0:
            return True
        return duration is not None and duration > 120.0

    def _request_mpv_stall_restart(
        self,
        *,
        playlist_id: int,
        reason: str,
        media_key: Optional[str] = None,
        skip_stall_count: bool = False,
    ) -> None:
        """mpv IPC socket gone during playback; restart service and let PlaybackService resume."""
        stall_count = 0
        advance_next = False
        if media_key and not skip_stall_count:
            stall_count, advance_next = self._record_stall_for_media(media_key)
        elif skip_stall_count:
            with self._stall_count_lock:
                advance_next = bool(self._stall_recovery_advance)
        self._mark_stall_restart_pending()
        self.logger.warning(
            "playlist: requesting mpv restart after playback stall",
            extra={
                "playlist_id": playlist_id,
                "reason": reason,
                "media_key": media_key,
                "stall_count": stall_count,
                "advance_next_item": advance_next,
                "stall_advance_threshold": self._stall_advance_threshold(),
            },
        )
        try:
            self._mpv_manager._schedule_hung_recovery()
        except Exception:
            pass

    def _try_midstream_network_reload(
        self,
        item: Dict[str, Any],
        *,
        playlist_id: int,
        reason: str,
        seek_to: Optional[float] = None,
    ) -> bool:
        """
        Reload ytdl/http stream via loadfile without systemd-restarting mpv.
        Used when time-pos freezes mid-roll but IPC still responds.

        When ``seek_to`` is set (proactive refresh), re-resolve URL and resume near
        the saved position instead of restarting from 0:00.
        """
        media_key = str(item.get("key") or item.get("path") or "")
        saved_seek = seek_to
        if saved_seek is None:
            saved_seek = self._snap_number(
                {"time-pos": self._mpv_get_light("time-pos", timeout=3.0)},
                "time-pos",
            )
        if not self._refresh_item_playback_path(item):
            self.logger.warning(
                "playlist: mid-stream reload skipped (URL refresh failed)",
                extra={
                    "playlist_id": playlist_id,
                    "media_key": media_key,
                    "reason": reason,
                },
            )
            return False
        path = str(item.get("path") or "")
        if not path.startswith(("http://", "https://", "ytdl://")):
            return False
        self.logger.info(
            "playlist: mid-stream network reload",
            extra={
                "playlist_id": playlist_id,
                "media_key": media_key,
                "reason": reason,
                "seek_to": saved_seek,
                "path_preview": path[:120],
            },
        )
        try:
            self._mpv_manager.set_playback_stream_opening(True)
        except Exception:
            pass
        try:
            normalized_headers, mpv_per_file_opts = self._apply_mpv_http_headers(
                item, stream_url=path
            )
            self._apply_mpv_ytdl_options(item, stream_url=path)
            load_cmd = self._mpv_loadfile_command(
                path, "replace", per_file_opts=mpv_per_file_opts
            )
            if path.startswith("ytdl://"):
                self._issue_ytdl_loadfile(load_cmd, media_key=media_key)
            else:
                self._issue_loadfile(
                    load_cmd,
                    media_key=media_key,
                    force=True,
                    timeout=self._network_loadfile_timeout_sec(path, is_network=True),
                )
            if not self._ensure_network_stream_started(
                item,
                path,
                normalized_headers=normalized_headers,
                load_cmd=load_cmd,
                load_ipc_ok=True,
            ):
                return False
            try:
                self._mpv_manager.set_playback_stream_opening(True)
            except Exception:
                pass
            if saved_seek is not None and saved_seek > 5.0:
                try:
                    self._mpv_manager._send_command(
                        {"command": ["seek", float(saved_seek), "absolute"]},
                        timeout=8.0,
                    )
                    self._mpv_manager._send_command(
                        {"command": ["set_property", "pause", "no"]},
                        timeout=3.0,
                    )
                except Exception:
                    pass
            self._stop_event.wait(timeout=8.0)
            tp_after = self._snap_number(
                {"time-pos": self._mpv_get_light("time-pos", timeout=3.0)},
                "time-pos",
            )
            if saved_seek is not None and saved_seek > 5.0:
                ok = (
                    tp_after is not None
                    and tp_after >= max(0.0, float(saved_seek) - 45.0)
                )
            else:
                ok = tp_after is not None and tp_after > 0.2
            if not ok:
                self.logger.warning(
                    "playlist: mid-stream reload did not resume playback",
                    extra={
                        "playlist_id": playlist_id,
                        "media_key": media_key,
                        "seek_to": saved_seek,
                        "time_pos_after": tp_after,
                    },
                )
            return ok
        except Exception as e:
            self.logger.warning(
                "playlist: mid-stream reload failed",
                extra={
                    "playlist_id": playlist_id,
                    "media_key": media_key,
                    "error": str(e),
                    "type": type(e).__name__,
                },
            )
            return False
        finally:
            try:
                self._mpv_manager.set_playback_stream_opening(False)
            except Exception:
                pass

    def _show_between_items_placeholder(self, *, network_next: bool = False) -> None:
        """Hide TTY/console between playlist items while the next loadfile is prepared."""
        if PlaybackConstants.is_wayland_backend():
            return
        if not network_next:
            # Local→local: replace loadfile is enough; logo loadfile blocks IPC when mpv is busy.
            return
        try:
            self._logo_manager.show_between_items_frame()
        except Exception:
            pass

    @staticmethod
    def _snap_bool(snap: Dict[str, Any], key: str) -> Optional[bool]:
        val = snap.get(key) if snap else None
        if isinstance(val, bool):
            return val
        return None

    @staticmethod
    def _snap_str(snap: Dict[str, Any], key: str) -> Optional[str]:
        snap = snap or {}
        val = snap.get(key)
        if val is None:
            return None
        return str(val)

    def _snap_number(self, snap: Dict[str, Any], key: str) -> Optional[float]:
        snap = snap or {}
        data = snap.get(key)
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            return float(data)
        if isinstance(data, str):
            s = data.strip()
            if not s or s.lower() in ("nan", "inf", "n/a", "none"):
                return None
            m = re.match(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$", s)
            if m:
                try:
                    return float(s)
                except ValueError:
                    return None
        return None

    def _normalize_mpv_http_headers(
        self,
        headers: Any,
        *,
        page_url: Optional[str] = None,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Normalize/sanitize HTTP headers before passing to MPV.

        Goals:
        - Avoid duplicate headers like `referer` + `Referer` (some CDNs return 400).
        - Keep a small allowlist (CDNs may reject browser-only headers like Sec-Fetch-*).
        - Ensure a single, correct Referer/Origin for VK/Rutube.
        """
        if not isinstance(headers, dict):
            headers = {}

        allow = {
            "user-agent",
            "referer",
            "referrer",
            "origin",
            "cookie",
            "accept",
        }

        # Canonicalize keys (Title-Case for readability; MPV doesn't care).
        out: Dict[str, str] = {}
        for k, v in headers.items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v).strip()
            if not ks or not vs:
                continue
            if ks.lower() not in allow:
                continue
            canon = "-".join([p.capitalize() for p in ks.lower().split("-")])
            out[canon] = vs

        # Ensure a single Referer.
        #
        # IMPORTANT: Provider CDNs change behavior over time. Empirically:
        # - VK/OKCDN requires page-level Referer/Origin.
        # - Rutube `river-*.rutube.ru` / `*.rtbcdn.ru` often expects Rutube page-level Referer/Origin
        #   (matches browser/curl behavior), so do not suppress it here. Prefer the ExternalMediaService
        #   sanitizer when attached for a single source of truth.
        prov_lc = (str(provider or "").strip().lower()) if provider is not None else ""
        su = str(stream_url or "")
        is_rutube_cdn = ("rutube" in prov_lc) and (
            "river-" in su or ".rtbcdn.ru/" in su or "rtbcdn.ru/" in su
        )
        ref = out.get("Referer") or out.get("Referrer") or out.get("referer")
        if not ref and page_url:
            ref = page_url
        if ref:
            out.pop("Referrer", None)
            out["Referer"] = str(ref)

        # Derive Origin from referer if not provided.
        if "Origin" not in out:
            base = out.get("Referer") or page_url
            if base and isinstance(base, str) and base.startswith(("http://", "https://")):
                try:
                    from urllib.parse import urlparse

                    p = urlparse(base)
                    if p.scheme and p.netloc:
                        out["Origin"] = f"{p.scheme}://{p.netloc}"
                except Exception:
                    pass

        # Set Accept defaults (some CDNs are picky; keep it minimal).
        out.setdefault("Accept", "*/*")

        # Ensure we always have a UA (MPV default can be too generic).
        out.setdefault(
            "User-Agent",
            "Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # Never send an empty Cookie header.
        if out.get("Cookie", "").strip() == "":
            out.pop("Cookie", None)
        ck = out.get("Cookie")
        if ck and len(ck) > 32768:
            out.pop("Cookie", None)

        return out

    def _sanitize_headers_for_mpv(
        self,
        headers: Any,
        *,
        page_url: Optional[str] = None,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """Prefer ExternalMediaService sanitizer when attached (single source of truth)."""
        svc = self._external_media_service
        if svc and hasattr(svc, "sanitize_mpv_http_headers"):
            try:
                return svc.sanitize_mpv_http_headers(
                    headers,
                    page_url=page_url,
                    stream_url=stream_url,
                    provider=provider,
                )
            except Exception:
                pass
        return self._normalize_mpv_http_headers(
            headers,
            page_url=page_url,
            stream_url=stream_url,
            provider=provider,
        )

    @staticmethod
    def _escape_mpv_key_value_list_token(s: str) -> str:
        """Escape a token for mpv's comma-separated key=value list options (e.g. --stream-lavf-o)."""
        return (
            str(s)
            .replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace("=", "\\=")
        )

    def _format_mpv_key_value_list(self, d: Dict[str, str]) -> str:
        parts: list[str] = []
        for k, v in (d or {}).items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v)
            if not ks or not vs:
                continue
            parts.append(
                f"{self._escape_mpv_key_value_list_token(ks)}={self._escape_mpv_key_value_list_token(vs)}"
            )
        return ",".join(parts)

    def _clear_mpv_http_options(self, *, fast: bool = False) -> None:
        """Reset per-stream HTTP options so a previous item cannot poison the next load."""
        timeout = 0.35 if fast else 3.0
        attempts = 1 if fast else None
        cmds = [
            ["set_property", "http-header-fields", ""],
            ["set_property", "user-agent", ""],
            ["set_property", "referrer", ""],
            ["set_property", "ytdl-format", ""],
        ]
        for cmd in cmds:
            try:
                self._mpv_manager._send_command(
                    {"command": cmd},
                    timeout=timeout,
                    max_attempts=attempts,
                )
            except Exception:
                pass

    def _build_mpv_stream_lavf_o_opts(
        self,
        headers: Dict[str, str],
        *,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Build libavformat options for network opens (used as per-file `stream-lavf-o` on loadfile).

        When mpv plays `edl://` sources from ytdl_hook, the underlying open can go through
        lavf/ffmpeg. Those opens may ignore `http-header-fields`, so we pass `stream-lavf-o`
        as a per-file option (mpv `loadfile` option list), plus `user-agent`/`referrer` fallbacks.

        IMPORTANT: Avoid duplicating UA/Referer between multiple mechanisms when possible.
        """
        if not headers:
            return {}
        ua = str(headers.get("User-Agent") or "").strip()
        ref = str(headers.get("Referer") or "").strip()
        cookie = str(headers.get("Cookie") or "").strip()
        if not ua and not ref and not cookie:
            return {}

        # ffmpeg expects CRLF-separated header lines in `headers`.
        #
        # IMPORTANT:
        # If we set the dedicated lavf `cookies` option, avoid also embedding a `Cookie:` header
        # in the generic `headers` blob. Some CDNs respond with HTTP 400 to duplicate Cookie headers.
        hdr_lines = []
        # Rutube CDN seems particularly sensitive to duplicated request context. For this path,
        # prefer the dedicated lavf `referer` option and avoid also sending `Referer:` via `headers`.
        try:
            prov_lc_hdr = (str(provider or "").strip().lower()) if provider is not None else ""
            su_hdr = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            is_rutube_cdn_hdr = (prov_lc_hdr == "rutube") and (
                "river-" in su_hdr or ".rtbcdn.ru/" in su_hdr or "rtbcdn.ru/" in su_hdr
            )
        except Exception:
            is_rutube_cdn_hdr = False
        for k, v in headers.items():
            if not k or not v:
                continue
            lk = str(k).strip().lower()
            if lk == "cookie" and cookie:
                continue
            if lk == "user-agent" and ua:
                # `user_agent` is already provided via the dedicated option; avoid duplicating it.
                continue
            if is_rutube_cdn_hdr and lk in ("referer", "referrer", "origin"):
                # `referer` is already provided via the dedicated option; Origin is not required for ffmpeg.
                continue
            if lk == "accept-language":
                continue
            # We'll pass UA/Referer both in dedicated fields and in `headers` when present.
            hdr_lines.append(f"{str(k).strip()}: {str(v).strip()}")
        hdr_blob = "\r\n".join([h for h in hdr_lines if h])

        opts: Dict[str, str] = {}
        if ua:
            opts["user_agent"] = ua
        if ref:
            # ffmpeg uses `referer` (single-r).
            opts["referer"] = ref
        if cookie:
            # ffmpeg/lavf accepts a cookie string via the `cookies` option; this can be more reliable
            # than relying only on `headers` for some CDNs.
            # Do not append terminators: some CDNs reject malformed cookie strings.
            opts["cookies"] = cookie

        # Rutube CDN: force HTTP/1.1. Empirically, curl/ffmpeg repros succeed with HTTP/1.1, while
        # mpv-embedded ffmpeg opens can differ in negotiation and trigger HTTP 400.
        try:
            prov_lc = (str(provider or "").strip().lower()) if provider is not None else ""
            su = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            is_rutube_cdn = (prov_lc == "rutube") and (
                "river-" in su or ".rtbcdn.ru/" in su or "rtbcdn.ru/" in su
            )
            if is_rutube_cdn:
                opts.setdefault("http_version", "1.1")
        except Exception:
            pass
        if hdr_blob:
            opts["headers"] = hdr_blob + "\r\n"

        if stream_url and str(stream_url).startswith(
            ("http://", "https://", "ytdl://")
        ):
            for k, v in self._lavf_network_timeout_opts().items():
                opts.setdefault(k, v)

        # mpv-level fallbacks; they can help non-lavf opens too.
        if ua:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "user-agent", ua]},
                    timeout=3.0,
                )
            except Exception:
                pass
        if ref:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "referrer", ref]},
                    timeout=3.0,
                )
            except Exception:
                pass
        return opts

    @staticmethod
    def _is_valid_lavf_option_key(key: str) -> bool:
        """Reject ytdl_hook/parser debris (e.g. en;q=0.9 split into en;q\\ AVOption keys)."""
        ks = str(key or "").strip()
        if not ks:
            return False
        if ";" in ks or "\\" in ks or "=" in ks:
            return False
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*$", ks))

    @staticmethod
    def _strip_accept_language_header_lines(blob: str) -> str:
        lines = []
        for line in str(blob or "").replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.lower().startswith("accept-language:"):
                continue
            lines.append(line.rstrip("\r"))
        return "\r\n".join(lines)

    def _merge_mpv_lavf_options(self, base: Any, extra: Dict[str, str]) -> Dict[str, str]:
        """Merge dict-like lavf options, normalizing keys/values as strings."""
        out: Dict[str, str] = {}
        if isinstance(base, dict):
            for k, v in base.items():
                if k is None or v is None:
                    continue
                ks = str(k).strip()
                vs = str(v).strip()
                if not ks or not vs:
                    continue
                if not self._is_valid_lavf_option_key(ks):
                    continue
                if ks == "headers":
                    vs = self._strip_accept_language_header_lines(vs)
                    if not vs.strip():
                        continue
                out[ks] = vs
        for k, v in (extra or {}).items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v).strip()
            if not ks or not vs:
                continue
            if not self._is_valid_lavf_option_key(ks):
                continue
            if ks == "headers":
                vs = self._strip_accept_language_header_lines(vs)
                if not vs.strip():
                    continue
            out[ks] = vs
        return out

    def _apply_mpv_lavf_headers_after_ytdl_hook(
        self,
        *,
        normalized_headers: Dict[str, str],
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
        timeout_sec: float = 8.0,
        skip_idle_wait: bool = False,
    ) -> None:
        """
        ytdl_hook often sets `file-local-options/stream-lavf-o` (cookies) *after* loadfile.
        That can overwrite our earlier lavf options. Re-apply/merge UA+Referer+headers once the
        cookies option appears so ffmpeg opens (EDL sources) have the right request context.
        """
        if not normalized_headers:
            return
        ua = str(normalized_headers.get("User-Agent") or "").strip()
        ref = str(normalized_headers.get("Referer") or "").strip()
        cookie = str(normalized_headers.get("Cookie") or "").strip()
        if not ua and not ref and not cookie:
            return

        # Build the extra dict we want to ensure exists in stream-lavf-o.
        #
        # If we also set the dedicated lavf `cookies` option, avoid putting `Cookie:` into the
        # generic `headers` blob to prevent duplicate Cookie headers (can trigger HTTP 400).
        hdr_lines = []
        for k, v in normalized_headers.items():
            if not k or not v:
                continue
            lk = str(k).strip().lower()
            if lk in ("user-agent", "referer", "referrer", "accept-language"):
                continue
            if lk == "cookie" and cookie:
                continue
            hdr_lines.append(f"{str(k).strip()}: {str(v).strip()}")
        hdr_blob = "\r\n".join([h for h in hdr_lines if h])
        extra: Dict[str, str] = {}
        if ua:
            extra["user_agent"] = ua
        if ref:
            extra["referer"] = ref
        if cookie:
            # Do not append terminators: some CDNs reject malformed cookie strings.
            extra["cookies"] = cookie

        # Same Rutube transport tweak as in the initial lavf set.
        try:
            prov_lc = (str(provider or "").strip().lower()) if provider is not None else ""
            su = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            is_rutube_cdn = (prov_lc == "rutube") and (
                "river-" in su or ".rtbcdn.ru/" in su or "rtbcdn.ru/" in su
            )
            if is_rutube_cdn:
                extra.setdefault("http_version", "1.1")
        except Exception:
            pass
        if hdr_blob:
            extra["headers"] = hdr_blob + "\r\n"

        # `file-local-options/*` is only meaningful while a file is being opened/played.
        if not skip_idle_wait:
            if not self._wait_mpv_leave_idle(timeout_sec=min(8.0, float(timeout_sec))):
                return

        # Wait briefly for ytdl_hook to populate cookies, then merge+set.
        deadline = time.monotonic() + max(0.2, float(timeout_sec))
        base_val: Any = None
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", "file-local-options/stream-lavf-o"]},
                    timeout=2.0,
                )
                if resp and resp.get("error") == "success":
                    base_val = resp.get("data")
                    # If cookies already exist, this is the most common ytdl_hook write point.
                    if isinstance(base_val, dict) and base_val.get("cookies"):
                        break
            except Exception:
                pass
            self._stop_event.wait(timeout=0.15)

        merged = self._merge_mpv_lavf_options(base_val, extra)
        try:
            # Log with a string message + extras; some logging pipelines drop dict-as-message.
            self.logger.info(
                "Reapplying stream-lavf-o after ytdl_hook",
                extra={
                    "event": "mpv_lavf_reapply_after_ytdl_hook",
                    "lavf_keys": sorted(list(merged.keys()))[:25],
                    "has_cookies": bool(isinstance(merged, dict) and merged.get("cookies")),
                    "has_user_agent": bool(isinstance(merged, dict) and merged.get("user_agent")),
                    "has_referer": bool(isinstance(merged, dict) and merged.get("referer")),
                    "has_headers": bool(isinstance(merged, dict) and merged.get("headers")),
                },
            )
            self._mpv_manager._send_command(
                {"command": ["set_property", "file-local-options/stream-lavf-o", merged]},
                timeout=5.0,
            )
        except Exception:
            pass
        # Also keep mpv fallbacks in sync.
        if ua:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "user-agent", ua]},
                    timeout=3.0,
                )
            except Exception:
                pass
        if ref:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "referrer", ref]},
                    timeout=3.0,
                )
            except Exception:
                pass

    def _apply_mpv_ytdl_options(self, item: dict, *, stream_url: str) -> None:
        """
        Some providers (notably VK) frequently resolve to separate DASH streams when using the
        default ytdl format selection. That can result in an `edl://` item that opens and then
        immediately ends (black screen + rapid on_after_end_file).

        Prefer muxed streams capped at 1080p for VK. Uncapped `best` often resolves to 4K H.264,
        which software-decodes on signage hardware and runs much hotter than typical Rutube HLS.
        """
        try:
            provider = str(item.get("provider") or "").strip().lower()
        except Exception:
            provider = ""
        try:
            url = str(stream_url or "")
        except Exception:
            url = ""

        if provider == "vkvideo" or "vkvideo.ru" in url or "vk.com/video" in url:
            try:
                self._mpv_manager._send_command(
                    {
                        "command": [
                            "set_property",
                            "ytdl-format",
                            PlaybackConstants.VK_YTDL_FORMAT,
                        ]
                    },
                    timeout=2.0,
                    max_attempts=1,
                )
            except Exception:
                pass

    def _apply_mpv_http_headers(self, item: dict, *, stream_url: str) -> tuple[Dict[str, str], Dict[str, Any]]:
        """
        Set MPV HTTP options for one playlist item. Always clears stale options first.

        Returns:
        - normalized headers dict (may be empty)
        - per-file mpv options for the next `loadfile` command (mpv 0.38+: use index -1 when passing options)
        """
        http_headers = item.get("http_headers") or {}

        page_url = item.get("page_url")
        if page_url is not None:
            page_url = str(page_url) if page_url else None
        provider = item.get("provider")
        if provider is not None:
            provider = str(provider) if provider else None

        # If the DB row doesn't have captured http_headers (common for older external-media rows),
        # still synthesize a minimal set for network loads from page_url. VK/OKCDN is very sensitive
        # to Referer; without it ffmpeg opens can return HTTP 400 even when cookies exist.
        is_network_url = isinstance(stream_url, str) and stream_url.startswith(
            ("http://", "https://", "ytdl://")
        )
        if not isinstance(http_headers, dict):
            http_headers = {}
        if not http_headers and not is_network_url:
            self._clear_mpv_http_options()
            return {}, {}

        normalized = self._sanitize_headers_for_mpv(
            http_headers,
            page_url=page_url,
            stream_url=stream_url,
            provider=provider,
        )
        is_ytdl_url = isinstance(stream_url, str) and stream_url.startswith("ytdl://")
        # ytdl:// opens carry Referer/UA via per-file loadfile opts; clearing globals while
        # mpv is busy (ytdl_hook) blocks IPC for tens of seconds with no benefit.
        if not is_ytdl_url:
            self._clear_mpv_http_options(fast=bool(self._mpv_manager._playback_session_active))

        per_file_opts: Dict[str, Any] = {}
        per_file_opts.update(self._collect_mpv_network_buffering_per_file(item, stream_url=stream_url))

        # For network streams opened via lavf/ffmpeg (incl. direct Rutube river/rtbcdn URLs),
        # pass `stream-lavf-o` as a per-file option on `loadfile` (do not use `set_property` while idle).
        is_network = isinstance(stream_url, str) and stream_url.startswith(("http://", "https://", "ytdl://"))
        if is_network and normalized:
            lavf = self._build_mpv_stream_lavf_o_opts(
                normalized,
                stream_url=stream_url,
                provider=provider,
            )
            lavf_blob = self._format_mpv_key_value_list(lavf)
            if lavf_blob:
                per_file_opts["stream-lavf-o"] = lavf_blob

        # Rutube direct CDN (river-*/rtbcdn): the actual open goes through lavf/ffmpeg.
        # Setting global `http-header-fields` in parallel (Cookie/Referer/Origin/UA) can
        # duplicate or fight lavf request context and some CDNs return 400.
        # Leave `http-header-fields` empty after _clear_mpv_http_options; rely on per-file `stream-lavf-o` + mpv user-agent/referrer.
        skip_global_http_header_fields = False
        try:
            prov_h = (str(provider or "").strip().lower()) if provider is not None else ""
            su_h = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            if prov_h == "rutube" and is_network and not str(stream_url or "").startswith(
                "ytdl://"
            ):
                if "river-" in su_h or ".rtbcdn.ru/" in su_h or "rtbcdn.ru/" in su_h:
                    skip_global_http_header_fields = True
        except Exception:
            pass

        if not skip_global_http_header_fields:
            try:
                header_lines = []
                for k, v in normalized.items():
                    if k is None or v is None:
                        continue
                    ks = str(k).strip()
                    vs = str(v).strip()
                    if not ks or not vs:
                        continue
                    header_lines.append(f"{ks}: {vs}")
                if header_lines:
                    self._mpv_manager._send_command(
                        {
                            "command": ["set_property", "http-header-fields", "\r\n".join(header_lines)],
                        },
                        timeout=2.0,
                        max_attempts=1,
                    )
                # For VK/OKCDN we prefer to keep UA/Referer here too: ytdl_hook may overwrite
                # lavf options later (cookies-only), but it won't clobber http-header-fields.
            except Exception:
                pass
        return normalized, per_file_opts

    def _collect_mpv_network_buffering_per_file(self, item: dict, *, stream_url: str) -> Dict[str, str]:
        """
        Collect per-file buffering options for external network streams to reduce microfreezes.

        This is best-effort and intentionally OFF by default.
        Enable with DSIGN_MPV_NETBUF=1 (or true/yes/on).
        """
        enabled = (os.getenv("DSIGN_MPV_NETBUF", "").strip().lower() in ("1", "true", "yes", "on"))
        if not enabled:
            return {}
        if not isinstance(stream_url, str) or not stream_url.startswith(("http://", "https://", "ytdl://")):
            return {}
        try:
            provider = str(item.get("provider") or "").strip().lower()
        except Exception:
            provider = ""
        su = stream_url.lower()
        is_external = (
            provider in ("vkvideo", "rutube")
            or "vkvideo.ru" in su
            or "vk.com/video" in su
            or "rutube.ru" in su
            or su.startswith("ytdl://")
        )
        if not is_external:
            return {}

        def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
            raw = os.getenv(name, "").strip()
            try:
                v = int(raw) if raw != "" else int(default)
            except Exception:
                v = int(default)
            if v < lo:
                return lo
            if v > hi:
                return hi
            return v

        cache_secs = _int_env("DSIGN_MPV_NETBUF_SECS", 12, lo=2, hi=60)
        max_bytes_mb = _int_env("DSIGN_MPV_NETBUF_MAX_MB", 96, lo=16, hi=512)
        back_bytes_mb = _int_env("DSIGN_MPV_NETBUF_BACK_MB", 16, lo=0, hi=128)
        readahead_secs = _int_env("DSIGN_MPV_NETBUF_READAHEAD_SECS", 20, lo=0, hi=120)

        return {
            "cache": "yes",
            "cache-secs": str(int(cache_secs)),
            "demuxer-max-bytes": str(int(max_bytes_mb) * 1024 * 1024),
            "demuxer-max-back-bytes": str(int(back_bytes_mb) * 1024 * 1024),
            "demuxer-readahead-secs": str(int(readahead_secs)),
        }

    def _mpv_loadfile_command(self, url: str, mode: str = "replace", *, per_file_opts: Optional[Dict[str, Any]] = None) -> list[Any]:
        """
        Build a `loadfile` IPC command compatible with mpv >= 0.38 (insert index arg).

        When `per_file_opts` is empty, use the legacy 3-arg form for maximum compatibility.
        When non-empty, pass `-1` as the insertion index placeholder and supply options as the 4th arg
        (mpv expects `MPV_FORMAT_NODE_MAP` with string values).
        """
        per_file_opts = self._augment_per_file_audio_opts(per_file_opts)
        if not per_file_opts:
            return ["loadfile", url, mode]

        opts: Dict[str, str] = {}
        for k, v in (per_file_opts or {}).items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            if not ks:
                continue
            opts[ks] = str(v)

        if not opts:
            return ["loadfile", url, mode]
        return ["loadfile", url, mode, -1, opts]

    def _wait_mpv_leave_idle(
        self,
        timeout_sec: float = 45.0,
        *,
        poll_sec: float = 0.5,
        snap_timeout: float = 8.0,
    ) -> bool:
        """
        After loadfile, mpv often reports idle-active=true until the demuxer opens.
        Our old loop treated idle as EOF and skipped network streams in ~1 tick.
        """
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        poll_sec = max(0.25, float(poll_sec))
        snap_timeout = max(3.0, min(20.0, float(snap_timeout)))
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            idle_raw = self._mpv_get_light("idle-active", timeout=snap_timeout)
            if isinstance(idle_raw, bool) and idle_raw is False:
                return True
            self._stop_event.wait(timeout=poll_sec)
        return False

    def _wait_mpv_network_idle_between_items(self, timeout_sec: float = 15.0) -> bool:
        """After network EOF, wait until mpv reports idle before the next loadfile."""
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            idle_raw = self._mpv_get_light("idle-active", timeout=0.35)
            if isinstance(idle_raw, bool) and idle_raw is True:
                return True
            self._stop_event.wait(timeout=0.2)
        return False

    def _prepare_mpv_network_reload(self) -> None:
        """Brief settle after a network item ended; per-file loadfile opts replace globals."""
        self._wait_mpv_network_idle_between_items(timeout_sec=4.0)

    def _network_loadfile_timeout_sec(self, path: str, *, is_network: bool) -> float:
        if str(path or "").startswith("ytdl://"):
            return self._ytdl_loadfile_ipc_timeout_sec()
        if is_network:
            return 45.0
        return 20.0

    def _apply_post_loadfile_playback_props(
        self,
        *,
        muted: bool,
        item_muted: Optional[bool] = None,
        profile_muted: Optional[bool] = None,
        rebind_audio: bool = False,
    ) -> None:
        """Pause/volume/mute after the demuxer is ready (mpv may ignore IPC while opening ytdl)."""
        if item_muted is not None:
            self._set_playback_mute_context(
                item_muted=bool(item_muted),
                profile_muted=bool(profile_muted),
            )
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "pause", "no"]},
                timeout=3.0,
                max_attempts=1,
            )
        except Exception:
            pass
        if rebind_audio or not self._audio_route_applied_for_play:
            if self._sync_settings_audio_route_to_mpv(cycle_ao=False):
                self._audio_route_applied_for_play = True
        self._sync_settings_volume_to_mpv()
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "mute", "yes" if muted else "no"]},
                timeout=2.0,
                max_attempts=1,
            )
        except Exception:
            pass
        if muted:
            self.logger.info(
                "Playback item muted",
                extra={"event": "playback_item_muted", "effective_muted": True},
            )
        self._kick_alsa_hardware_after_demuxer()
        self._ensure_mpv_alsa_pcm_open()
        self._log_mpv_audio_state(event="post_loadfile_audio_state")

    def _prepare_local_audio_after_loadfile(
        self,
        *,
        muted: bool,
        skip_load: bool,
        item_muted: Optional[bool] = None,
        profile_muted: Optional[bool] = None,
    ) -> bool:
        """
        After audio loadfile, wait until the demuxer opens before EOF polling.
        Cycle 2+ reloads can report idle-active briefly; without this wait the loop
        treats the item as finished instantly (silent skip).
        """
        if not skip_load:
            if not self._wait_mpv_leave_idle(
                timeout_sec=30.0, poll_sec=0.4, snap_timeout=6.0
            ):
                if self._stop_event.is_set():
                    return False
                self.logger.warning(
                    "Audio demuxer did not leave idle after loadfile",
                    extra={"event": "audio_demuxer_idle_timeout"},
                )
                return False
        self._apply_post_loadfile_playback_props(
            muted=muted,
            item_muted=item_muted,
            profile_muted=profile_muted,
            rebind_audio=False,
        )
        return True

    def _set_playback_active_marker(self, active: bool) -> None:
        marker = Path("/run/dsign/playback-active")
        try:
            if active:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("1", encoding="utf-8")
            elif marker.is_file():
                marker.unlink()
        except Exception as exc:
            self.logger.warning(
                "playback-active marker failed",
                extra={"event": "playback_active_marker", "active": active, "error": str(exc)},
            )

    def _wait_mpv_network_demuxer_ready(self, *, timeout_sec: float = 45.0, poll_sec: float = 0.25) -> bool:
        """
        After leave-idle, mpv can briefly report idle-active=false while HLS demuxer has not opened yet.
        Treat as failure if we return to idle before demuxer/path indicates an active open — avoids false
        instant_eof when loadfile fails quickly (403, TLS, CDN).
        """
        deadline = time.monotonic() + max(2.0, float(timeout_sec))
        poll_tick = 0
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            poll_tick += 1
            idle_raw = self._mpv_get_light("idle-active", timeout=0.35)
            if isinstance(idle_raw, bool) and idle_raw is True:
                return False
            if poll_tick % 3 == 0:
                eof_raw = self._mpv_get_light("eof-reached", timeout=0.35)
                if self._snap_bool({"eof-reached": eof_raw}, "eof-reached") is True:
                    return False

            dem_raw = self._mpv_get_light("demuxer", timeout=0.35)
            dem = self._snap_str({"demuxer": dem_raw}, "demuxer")
            if dem and str(dem).strip():
                return True
            if poll_tick % 2 == 0:
                soc_raw = self._mpv_get_light("stream-open-filename", timeout=0.35)
                soc = self._snap_str({"stream-open-filename": soc_raw}, "stream-open-filename")
                if soc and len(str(soc).strip()) > 8:
                    return True
                pth_raw = self._mpv_get_light("path", timeout=0.35)
                pth = self._snap_str({"path": pth_raw}, "path")
                if pth and len(str(pth).strip()) > 8 and not str(pth).startswith("ytdl://"):
                    return True
                dct_raw = self._mpv_get_light("demuxer-cache-time", timeout=0.35)
                dct = self._snap_number({"demuxer-cache-time": dct_raw}, "demuxer-cache-time")
                if dct is not None and dct > 0.02:
                    return True

            self._stop_event.wait(timeout=max(0.1, float(poll_sec)))
        return False

    def _wait_mpv_stream_ready(
        self,
        expected_url: str,
        timeout_sec: Optional[float] = None,
        poll_sec: float = 0.5,
    ) -> bool:
        """
        Some mpv builds never expose `eof-reached` for network streams (always 'property unavailable').
        Detect that demuxer/decoder actually started using time-pos / duration.

        For `ytdl://` items, MPV can spend a noticeable amount of time in the ytdl_hook resolution
        subprocess, and `time-pos`/`duration` may remain unavailable until after `path` switches
        from the virtual `ytdl://...` URL to a real stream URL. In that case, treat `path` switching
        away from `ytdl://` as progress/readiness.
        """
        def _float_env(name: str, default: float, *, lo: float, hi: float) -> float:
            try:
                v = float((os.getenv(name) or "").strip())
            except ValueError:
                return default
            return max(lo, min(hi, v))

        if timeout_sec is None:
            timeout_sec = _float_env("DSIGN_MPV_STREAM_READY_SECS", 120.0, lo=15.0, hi=600.0)
        grace_sec = _float_env("DSIGN_MPV_STREAM_READY_GRACE_SEC", 45.0, lo=5.0, hi=300.0)

        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        grace_until = time.monotonic() + grace_sec
        exp = (expected_url or "").strip()
        is_ytdl = exp.startswith("ytdl://")
        # For ytdl:// resolution on low-power devices, be gentle with IPC:
        # - ytdl_hook may take >10s
        # - mpv may update stream-open-filename earlier than time-pos/duration
        heavy_every = 2 if is_ytdl else 1
        tick = 0

        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False

            snap_loop: Dict[str, Any] = {}
            if is_ytdl:
                snap_loop = self._mpv_snapshot(
                    [
                        "stream-open-filename",
                        "path",
                        "demuxer-cache-time",
                        "demuxer-cache-duration",
                        "demuxer",
                        "time-pos",
                        "duration",
                    ],
                    timeout=2.0,
                )
                cur_stream = self._snap_str(snap_loop, "stream-open-filename")
                if cur_stream and not str(cur_stream).startswith("ytdl://"):
                    return True
                cur_path = self._snap_str(snap_loop, "path")
                if cur_path and not str(cur_path).startswith("ytdl://"):
                    return True
                dct = self._snap_number(snap_loop, "demuxer-cache-time")
                if dct is not None and dct > 0.05:
                    return True
                dcdur = self._snap_number(snap_loop, "demuxer-cache-duration")
                if dcdur is not None and dcdur > 0.05:
                    return True
                dem = self._snap_str(snap_loop, "demuxer")
                if dem and str(dem).strip():
                    tp_y = self._snap_number(snap_loop, "time-pos")
                    if tp_y is not None:
                        return True
                    dur_y = self._snap_number(snap_loop, "duration")
                    if dur_y is not None and dur_y > 0:
                        return True
            else:
                snap_loop = self._mpv_snapshot(
                    [
                        "stream-open-filename",
                        "path",
                        "demuxer-cache-time",
                        "demuxer-cache-duration",
                        "idle-active",
                        "eof-reached",
                        "paused-for-cache",
                        "core-idle",
                        "time-pos",
                        "duration",
                    ],
                    timeout=2.0,
                )
                cur_stream = self._snap_str(snap_loop, "stream-open-filename")
                cur_path = self._snap_str(snap_loop, "path")
                if (cur_stream and len(str(cur_stream)) > 0) or (
                    cur_path and len(str(cur_path)) > 0
                ):
                    # do not early-return; keep probing time-pos/duration below
                    pass
                dct = self._snap_number(snap_loop, "demuxer-cache-time")
                if dct is not None and dct > 0.05:
                    return True
                dcdur = self._snap_number(snap_loop, "demuxer-cache-duration")
                if dcdur is not None and dcdur > 0.05:
                    return True

                idle = self._snap_bool(snap_loop, "idle-active")
                eof = self._snap_bool(snap_loop, "eof-reached")
                paused_cache = self._snap_bool(snap_loop, "paused-for-cache")
                core_idle = self._snap_bool(snap_loop, "core-idle")

                if idle is False and eof is not True:
                    if paused_cache is True:
                        pass
                    elif paused_cache is False:
                        return True
                    else:
                        if core_idle is False:
                            return True
                        if time.monotonic() >= grace_until:
                            return True
            tick += 1
            if heavy_every > 1 and (tick % heavy_every) != 0:
                self._stop_event.wait(timeout=max(0.15, float(poll_sec)))
                continue
            tp = self._snap_number(snap_loop, "time-pos")
            if tp is not None:
                return True
            dur = self._snap_number(snap_loop, "duration")
            if dur is not None and dur > 0:
                return True
            self._stop_event.wait(timeout=max(0.1, float(poll_sec)))
        return False

    def _detect_mpv_instant_eof(self, *, window_sec: float = 2.5) -> bool:
        """
        Detect a pathological case where MPV opens a file/stream and immediately returns to idle.
        This happens with some VK `edl://` selections (separate streams / blocked URL) and should
        be treated as a start failure (so we backoff instead of busy-looping).

        Require evidence the demuxer actually opened — otherwise idle flicker before HLS attaches
        looks like instant EOF on Rutube/CDN.
        """
        deadline = time.monotonic() + max(0.2, float(window_sec))
        saw_not_idle = False
        saw_demuxer = False
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            idle_raw = self._mpv_get_light("idle-active", timeout=0.35)
            idle = self._snap_bool({"idle-active": idle_raw}, "idle-active")
            if idle is False:
                saw_not_idle = True
            dem_raw = self._mpv_get_light("demuxer", timeout=0.35)
            dem = self._snap_str({"demuxer": dem_raw}, "demuxer")
            if dem and str(dem).strip():
                saw_demuxer = True
            else:
                dct_raw = self._mpv_get_light("demuxer-cache-time", timeout=0.35)
                if self._snap_number({"demuxer-cache-time": dct_raw}, "demuxer-cache-time") not in (
                    None,
                    0.0,
                ):
                    saw_demuxer = True

            if saw_not_idle and idle is True and saw_demuxer:
                return True
            self._stop_event.wait(timeout=0.1)
        return False

    def _is_external_stream_provider(
        self, *, provider: Optional[str] = None, stream_url: Optional[str] = None
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

    @staticmethod
    def _float_env(name: str, default: float, *, lo: float, hi: float) -> float:
        try:
            v = float((os.getenv(name) or str(default)).strip())
        except ValueError:
            v = float(default)
        return max(lo, min(hi, v))

    def _network_eof_advance_sec(self) -> float:
        """Finish Rutube/VK HLS slightly before reported duration to avoid demuxer hang."""
        return self._float_env("DSIGN_MPV_NETWORK_EOF_ADVANCE_SEC", 8.0, lo=0.0, hi=60.0)

    def _network_near_eof_stagnation_sec(self) -> float:
        """When near duration, treat frozen time-pos as EOF quickly (not mpv restart)."""
        return self._float_env(
            "DSIGN_MPV_NETWORK_NEAR_EOF_STAGNATION_SEC", 15.0, lo=5.0, hi=120.0
        )

    def _network_stream_near_eof(
        self, *, time_pos: Optional[float], duration: Optional[float]
    ) -> bool:
        if (
            time_pos is None
            or duration is None
            or duration <= 0.5
            or time_pos < 0.0
        ):
            return False
        return time_pos + self._network_eof_advance_sec() >= duration

    def _lavf_network_timeout_opts(self) -> Dict[str, str]:
        """ffmpeg/lavf read timeouts (microseconds) — avoid indefinite demuxer blocks."""
        sec = self._float_env("DSIGN_MPV_LAVF_TIMEOUT_SEC", 15.0, lo=5.0, hi=120.0)
        us = str(int(sec * 1_000_000))
        return {"timeout": us, "rw_timeout": us}

    def _proactive_refresh_interval_minutes_raw(self) -> str:
        return (
            os.getenv("DSIGN_MPV_PROACTIVE_REFRESH_INTERVAL_MIN")
            or os.getenv("DSIGN_MPV_PROACTIVE_REFRESH_MIN")
            or "25"
        ).strip()

    def _proactive_refresh_enabled(self) -> bool:
        return self._proactive_refresh_interval_minutes_raw().lower() not in (
            "0",
            "false",
            "no",
            "off",
            "",
        )

    def _proactive_refresh_interval_sec(self) -> Optional[float]:
        """Recurring mid-roll reload interval (default 25 min). None when disabled."""
        if not self._proactive_refresh_enabled():
            return None
        try:
            minutes = float(self._proactive_refresh_interval_minutes_raw())
        except ValueError:
            minutes = 25.0
        minutes = max(5.0, min(120.0, minutes))
        return minutes * 60.0

    def _proactive_refresh_min_duration_sec(self) -> float:
        """Optional floor: skip proactive refresh on short rolls. 0 = any length."""
        return self._float_env(
            "DSIGN_MPV_PROACTIVE_REFRESH_MIN_DURATION_SEC", 0.0, lo=0.0, hi=7200.0
        )

    def _tail_mpv_log_segments(self, *, limit: int = 5) -> List[str]:
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

    def _log_ipc_dead_diagnostics(
        self,
        *,
        playlist_id: int,
        media_key: Optional[str],
        time_pos: Optional[float],
        duration: Optional[float],
        stall_polls: int,
    ) -> None:
        self.logger.warning(
            "playlist_eof_stall: IPC dead context snapshot",
            extra={
                "playlist_id": playlist_id,
                "media_key": media_key,
                "time_pos": time_pos,
                "duration": duration,
                "stall_polls": stall_polls,
                "mpv_log_segments": self._tail_mpv_log_segments(limit=5),
            },
        )

    def _wait_mpv_video_end(
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

        - Prefer eof-reached == True when MPV returns a real boolean.
        - Else for streams: after stream_ready, treat return to idle-active as end-of-file.
        - Else for local files: same idle fallback after a short grace period.

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
                self._mpv_manager.set_playback_network_active(True)
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
        eof_capable = is_network and self._is_external_stream_provider(
            provider=provider, stream_url=stream_url
        )
        eof_advance_sec = self._network_eof_advance_sec() if eof_capable else 0.0
        near_eof_stagnation_sec = (
            self._network_near_eof_stagnation_sec() if eof_capable else stagnation_sec
        )
        midstream_reload_attempts = 0
        proactive_refresh_next_at: Optional[float] = None
        use_eof_events = False
        if is_network:
            try:
                self._mpv_manager.enable_playback_eof_events()
                use_eof_events = bool(self._mpv_manager._playback_eof_events_enabled)
                if use_eof_events:
                    drained = self._mpv_manager.drain_playback_events("end-file")
                    if drained:
                        self.logger.debug(
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
                self._clear_stall_count_for_media(media_key)
            self.logger.info(
                "Playlist item finished",
                extra={
                    "playlist_id": playlist_id,
                    "is_network": is_network,
                    "media_key": media_key,
                    "reason": reason,
                },
            )

        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            if time.time() - start > 6 * 3600:
                break

            poll_wait = max(0.2, float(poll_sec))
            if use_eof_events and time.monotonic() >= grace_until:
                ev = self._mpv_manager.wait_playback_event(
                    "end-file", timeout=poll_wait
                )
                if ev is not None:
                    ev_reason = str(ev.get("reason") or "").strip().lower()
                    # loadfile replace emits end-file/stop for the *previous* file — not EOF.
                    if ev_reason == "eof":
                        _finish_video_item("mpv_end_file_eof")
                        break
                    if ev_reason in ("stop", "quit"):
                        self._mpv_manager.drain_playback_events("end-file")

            snap_timeout = 10.0 if is_network else 4.0
            try:
                raw_to = (os.getenv("DSIGN_MPV_EOF_SNAPSHOT_TIMEOUT_SEC") or "").strip()
                if raw_to:
                    snap_timeout = float(raw_to)
            except ValueError:
                pass
            snap_timeout = max(2.0, min(20.0, snap_timeout))

            poll_tick += 1
            tp_raw = self._mpv_get_light("time-pos", timeout=snap_timeout)
            tp = self._snap_number({"time-pos": tp_raw}, "time-pos")
            if is_audio and tp is not None and tp > 0.05:
                playback_started = True

            idle_raw: Optional[Any] = None
            if is_network:
                if poll_tick % 2 == 0:
                    idle_raw = self._mpv_get_light("idle-active", timeout=snap_timeout)
            else:
                idle_raw = self._mpv_get_light("idle-active", timeout=snap_timeout)

            dur: Optional[float] = None
            poll_duration = (not is_network) or eof_capable
            if poll_duration and poll_tick % 3 == 0:
                dur_raw = self._mpv_get_light("duration", timeout=snap_timeout)
                dur = self._snap_number({"duration": dur_raw}, "duration")
            if dur is not None and dur > 0.5:
                last_duration = dur

            min_refresh_duration = self._proactive_refresh_min_duration_sec()
            duration_long_enough = min_refresh_duration <= 0 or (
                (last_duration is not None and last_duration >= min_refresh_duration)
                or (tp is not None and tp >= min_refresh_duration)
            )
            refresh_interval_sec = self._proactive_refresh_interval_sec()
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
                    if self._try_midstream_network_reload(
                        item,
                        playlist_id=playlist_id,
                        reason="proactive_refresh",
                        seek_to=refresh_pos,
                    ):
                        self._mpv_manager.drain_playback_events("end-file")
                        last_time_pos = refresh_pos
                        last_time_pos_change = time.monotonic()
                        consecutive_ipc_stall = 0
                        last_ipc_ok = time.monotonic()
                        proactive_refresh_next_at = refresh_pos + refresh_interval_sec
                        self.logger.info(
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
                    near_eof_ipc = eof_capable and self._network_stream_near_eof(
                        time_pos=last_time_pos, duration=last_duration
                    )
                    if near_eof_ipc:
                        self.logger.info(
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
                    midstream_advance = self._should_advance_after_midstream_ipc_failure(
                        is_network=is_network,
                        eof_capable=eof_capable,
                        time_pos=last_time_pos,
                        duration=last_duration,
                    )
                    if socket_missing:
                        if midstream_advance:
                            self.logger.warning(
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
                            self._set_stall_recovery_advance()
                            self._request_mpv_stall_restart(
                                playlist_id=playlist_id,
                                reason="socket_missing_midstream",
                                media_key=media_key,
                                skip_stall_count=True,
                            )
                            return False
                        self.logger.warning(
                            "playlist_eof_stall: MPV socket missing during EOF wait",
                            extra={
                                "playlist_id": playlist_id,
                                "is_network": is_network,
                                "stall_polls": consecutive_ipc_stall,
                            },
                        )
                        self._request_mpv_stall_restart(
                            playlist_id=playlist_id,
                            reason="socket_missing_eof",
                            media_key=media_key,
                        )
                        return False
                    if midstream_advance:
                        self.logger.warning(
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
                        self._set_stall_recovery_advance()
                        self._request_mpv_stall_restart(
                            playlist_id=playlist_id,
                            reason="ipc_dead_midstream",
                            media_key=media_key,
                            skip_stall_count=True,
                        )
                        return False
                    self._log_ipc_dead_diagnostics(
                        playlist_id=playlist_id,
                        media_key=media_key,
                        time_pos=last_time_pos,
                        duration=last_duration,
                        stall_polls=consecutive_ipc_stall,
                    )
                    self.logger.warning(
                        "playlist_eof_stall: MPV IPC dead during EOF wait; requesting restart",
                        extra={
                            "playlist_id": playlist_id,
                            "is_network": is_network,
                            "media_key": media_key,
                            "stall_polls": consecutive_ipc_stall,
                        },
                    )
                    self._request_mpv_stall_restart(
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
                    near_end = self._network_stream_near_eof(
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
                            self.logger.info(
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
                        self.logger.warning(
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
                            max_reload = self._midstream_reload_max_attempts()
                            if (
                                eof_capable
                                and item is not None
                                and midstream_reload_attempts < max_reload
                            ):
                                midstream_reload_attempts += 1
                                if self._try_midstream_network_reload(
                                    item,
                                    playlist_id=playlist_id,
                                    reason="time_pos_stagnation",
                                    seek_to=tp,
                                ):
                                    self._mpv_manager.drain_playback_events(
                                        "end-file"
                                    )
                                    last_time_pos = None
                                    last_time_pos_change = time.monotonic()
                                    consecutive_ipc_stall = 0
                                    last_ipc_ok = time.monotonic()
                                    continue
                            self._request_mpv_stall_restart(
                                playlist_id=playlist_id,
                                reason="time_pos_stagnation",
                                media_key=media_key,
                            )
                            return False
                        _finish_video_item("time_pos_stagnation")
                        break

            idle = self._snap_bool({"idle-active": idle_raw}, "idle-active")
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
            else:
                consecutive_idle = 0

            if not use_eof_events or time.monotonic() < grace_until:
                self._stop_event.wait(timeout=poll_wait)
        if is_network:
            try:
                self._mpv_manager.set_playback_network_active(False)
            except Exception:
                pass
        if self._stall_restart_was_requested():
            return False
        return (
            not self._stop_event.is_set()
            and self._active_playlist_id == playlist_id
        )

    def _log_mpv_network_debug_snapshot(self, *, media_key: str, url: str) -> None:
        """
        Best-effort debug snapshot for stubborn network streams.
        This helps distinguish 'mpv never tried to open URL' vs 'opened but blocked (403/TLS)'.
        """
        try:
            snap = self._mpv_manager.get_properties_snapshot(
                [
                    "path",
                    "stream-open-filename",
                    "media-title",
                    "file-format",
                    "demuxer",
                    "idle-active",
                    "core-idle",
                    "time-pos",
                    "duration",
                ],
                timeout=2.0,
            )
            mpv_props = {
                k: snap.get(k)
                for k in (
                    "path",
                    "stream-open-filename",
                    "media-title",
                    "file-format",
                    "demuxer",
                )
            }
            idle = self._snap_bool(snap, "idle-active")
            core_idle = self._snap_bool(snap, "core-idle")
            tp = self._snap_number(snap, "time-pos")
            dur = self._snap_number(snap, "duration")
            self.logger.warning(
                "MPV network stream debug snapshot",
                extra={
                    "media_key": media_key,
                    "url_preview": str(url)[:160],
                    "idle_active": idle,
                    "core_idle": core_idle,
                    "time_pos": tp,
                    "duration": dur,
                    "mpv_props": mpv_props,
                },
            )
        except Exception:
            # never let diagnostics break playback
            pass

    def _play_local_video_engine(
        self,
        *,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int,
        profile_muted: bool,
        profile_settings: Dict[str, Any],
        playlist: Any,
        mode: str,
    ) -> bool:
        """A2 single local video (loop-file=inf) or A1 mpv internal M3U playlist."""
        from ..models import PlaybackStatus

        start_index = int(start_index or 0) % len(items)
        self._active_playlist_id = playlist_id
        self._audio_route_applied_for_play = False
        try:
            self._logo_manager.ensure_mpv_video_output()
        except Exception:
            pass
        try:
            self._mpv_manager.set_playback_session_active(True)
        except Exception:
            pass
        self._set_loop_position(start_index, len(items))

        self.logger.info(
            "Starting local video playback mode",
            extra={
                "playlist_id": playlist_id,
                "mode": mode,
                "items_count": len(items),
                "start_index": start_index,
            },
        )

        ordered_indices: Optional[List[int]] = None
        if mode == "local_single":
            item = items[start_index]
            path = str(item.get("path") or "")
            media_key = str(item.get("key") or path)
            self._set_current_media_label(self._item_media_label(item))
            self._mpv_set_local_playback_props(
                loop_file="inf",
                loop_playlist=False,
                prefetch=False,
            )
            self._prepare_mpv_audio_before_loadfile()
            load_resp = self._safe_loadfile(
                path,
                media_key=media_key,
                is_video=True,
                timeout=10.0,
            )
            if not load_resp:
                raise RuntimeError(f"safe_loadfile failed for local video: {path}")
            self._apply_post_loadfile_playback_props(
                muted=self._effective_playback_muted(
                    item_muted=bool(item.get("muted", False)),
                    profile_muted=profile_muted,
                ),
                item_muted=bool(item.get("muted", False)),
                profile_muted=profile_muted,
            )
            self._apply_item_mute_property(item, profile_muted=profile_muted)
            self._set_last_good_playback(playlist_id, start_index, media_key, len(items))
            thread_target = self._run_single_local_video_loop
            thread_args: tuple = (playlist_id, items, profile_muted)
        else:
            m3u_path = self._write_local_video_m3u(playlist_id, items, start_index)
            ordered_indices = [(start_index + offset) % len(items) for offset in range(len(items))]
            first_item = items[ordered_indices[0]]
            media_key = f"local-m3u-{playlist_id}"
            self._set_current_media_label(self._item_media_label(first_item))
            self._mpv_set_local_playback_props(
                loop_file="no",
                loop_playlist=True,
                prefetch=True,
            )
            self._prepare_mpv_audio_before_loadfile()
            if not self._safe_loadfile(
                str(m3u_path),
                media_key=media_key,
                is_video=True,
                timeout=15.0,
            ):
                raise RuntimeError(f"safe_loadfile failed for local M3U: {m3u_path}")
            self._apply_post_loadfile_playback_props(
                muted=self._effective_playback_muted(
                    item_muted=bool(first_item.get("muted", False)),
                    profile_muted=profile_muted,
                ),
                item_muted=bool(first_item.get("muted", False)),
                profile_muted=profile_muted,
            )
            self._apply_item_mute_property(first_item, profile_muted=profile_muted)
            self._set_last_good_playback(
                playlist_id,
                ordered_indices[0],
                str(first_item.get("key") or first_item.get("path") or ""),
                len(items),
            )
            thread_target = self._run_local_mpv_playlist_loop
            thread_args = (playlist_id, items, ordered_indices, profile_muted)

        playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
        playback.playlist_id = playlist_id
        playback.status = "playing"
        self.db_session.add(playback)
        self.db_session.commit()

        self._play_thread = Thread(target=thread_target, args=thread_args, daemon=True)
        self._play_thread.start()

        try:
            if self.socketio:
                self.socketio.emit(
                    "playback_update",
                    {
                        "status": "playing",
                        "playlist_id": playlist.id,
                        "current_media": self._get_current_media_label(),
                        "playlist": {"id": playlist.id, "name": playlist.name},
                        "settings": profile_settings,
                        "playback_mode": mode,
                    },
                )
        except Exception:
            pass
        return True

    def _single_local_video_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        profile_muted: bool,
    ) -> None:
        """A2: mpv loops via loop-file=inf — thread only tracks stop/resume state."""
        if not items:
            return
        item = items[0]
        media_key = str(item.get("key") or item.get("path") or "")
        self._mpv_manager.set_playback_session_active(True)
        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            self._set_loop_position(0, 1)
            self._publish_current_media(playlist_id, item)
            self._set_last_good_playback(playlist_id, 0, media_key, 1)
            self._stop_event.wait(timeout=2.0)

    def _run_single_local_video_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        profile_muted: bool,
    ) -> None:
        try:
            with self._app_context():
                self._single_local_video_loop(playlist_id, items, profile_muted)
        except Exception as e:
            self.logger.error(
                "Single local video loop crashed",
                extra={
                    "playlist_id": playlist_id,
                    "error": str(e),
                    "type": type(e).__name__,
                    "stack_trace": traceback.format_exc(),
                },
            )
            cb = self._slideshow_crash_callback
            if cb is not None:
                try:
                    cb()
                except Exception as cb_exc:
                    self.logger.warning(
                        "Slideshow crash callback failed",
                        extra={"error": str(cb_exc), "type": type(cb_exc).__name__},
                    )

    def _local_mpv_playlist_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        ordered_indices: List[int],
        profile_muted: bool,
    ) -> None:
        """A1: monitor mpv internal playlist-pos; zero-gap transitions between local files."""
        self._mpv_manager.set_playback_session_active(True)
        last_pos: Optional[int] = None
        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            raw_pos = self._mpv_manager.get_property_light("playlist-pos", timeout=2.0)
            if raw_pos is not None:
                try:
                    pos = int(raw_pos)
                except (TypeError, ValueError):
                    pos = None
                if pos is not None and 0 <= pos < len(ordered_indices) and pos != last_pos:
                    last_pos = pos
                    item_index = ordered_indices[pos]
                    item = items[item_index]
                    self._set_loop_position(item_index, len(items))
                    self._publish_current_media(playlist_id, item)
                    self._apply_item_mute_property(item, profile_muted=profile_muted)
                    self._set_last_good_playback(
                        playlist_id,
                        item_index,
                        str(item.get("key") or item.get("path") or ""),
                        len(items),
                    )
            self._stop_event.wait(timeout=0.5)

    def _run_local_mpv_playlist_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        ordered_indices: List[int],
        profile_muted: bool,
    ) -> None:
        try:
            with self._app_context():
                self._local_mpv_playlist_loop(
                    playlist_id,
                    items,
                    ordered_indices,
                    profile_muted,
                )
        except Exception as e:
            self.logger.error(
                "Local mpv playlist loop crashed",
                extra={
                    "playlist_id": playlist_id,
                    "error": str(e),
                    "type": type(e).__name__,
                    "stack_trace": traceback.format_exc(),
                },
            )
            cb = self._slideshow_crash_callback
            if cb is not None:
                try:
                    cb()
                except Exception as cb_exc:
                    self.logger.warning(
                        "Slideshow crash callback failed",
                        extra={"error": str(cb_exc), "type": type(cb_exc).__name__},
                    )

    def _stop_play_thread(
        self,
        *,
        preserve_stall_tracking: bool = False,
        preserve_loop_position: bool = False,
    ):
        if self._play_thread and self._play_thread.is_alive():
            self._stop_event.set()
            try:
                self._play_thread.join(timeout=2.0)
            except Exception:
                pass
        self._play_thread = None
        self._stop_event.clear()
        self._clear_stall_restart_pending()
        if not preserve_stall_tracking:
            self._reset_stall_tracking()
        self._active_playlist_id = None
        self._preloaded_stream_ready = False
        self._preloaded_load_cmd = None
        self._clear_current_media_label(emit=False)
        if not preserve_loop_position:
            self._clear_loop_position()
        self._set_playback_active_marker(False)
        try:
            self._mpv_manager.set_playback_session_active(False)
        except Exception:
            pass

    def _run_manual_slideshow_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int = 0,
        *,
        first_item_preloaded: bool = False,
        profile_muted: bool = False,
    ) -> None:
        """Thread entry: push Flask app context before DB-backed external media refresh."""
        try:
            with self._app_context():
                self._manual_slideshow_loop(
                    playlist_id,
                    items,
                    start_index,
                    first_item_preloaded=first_item_preloaded,
                    profile_muted=profile_muted,
                )
        except Exception as e:
            self.logger.error(
                "Slideshow loop crashed",
                extra={
                    "playlist_id": playlist_id,
                    "start_index": start_index,
                    "error": str(e),
                    "type": type(e).__name__,
                    "stack_trace": traceback.format_exc(),
                },
            )
            cb = self._slideshow_crash_callback
            if cb is not None:
                try:
                    cb()
                except Exception as cb_exc:
                    self.logger.warning(
                        "Slideshow crash callback failed",
                        extra={
                            "error": str(cb_exc),
                            "type": type(cb_exc).__name__,
                        },
                    )

    def _manual_slideshow_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int = 0,
        *,
        first_item_preloaded: bool = False,
        profile_muted: bool = False,
    ):
        """
        Manual playback loop that enforces per-item durations for images and plays videos to EOF.
        Runs in a background thread; advances images by sleeping for their duration and videos by
        polling mpv properties until EOF.

        ``profile_muted``: playlist overrides mute flag (combined with per-file ``muted``).
        """
        self.logger.info("Starting manual playback loop", extra={"playlist_id": playlist_id, "items_count": len(items)})

        if not items:
            return
        self._mpv_manager.set_playback_session_active(True)
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
            while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
                loop_cycle += 1
                cycle_network_attempted = 0
                cycle_network_failed = 0
                self.logger.debug(
                    "Playlist loop cycle",
                    extra={"playlist_id": playlist_id, "cycle": loop_cycle, "items": len(items)},
                )
                stall_abort = False
                net_open_cycle_abort = False
                # Iterate cyclically starting from start_index.
                for offset in range(len(items)):
                    item_index = (start_index + offset) % len(items)
                    item = items[item_index]
                    self._set_loop_position(item_index, len(items))
                    if self._stop_event.is_set() or self._active_playlist_id != playlist_id:
                        break

                    path = item["path"]
                    is_video = item["is_video"]
                    is_audio = bool(item.get("is_audio"))
                    media_key = str(item.get("key") or path)
                    self._publish_current_media(playlist_id, item)
                    self.logger.info(
                        "Playlist item starting",
                        extra={
                            "playlist_id": playlist_id,
                            "cycle": loop_cycle,
                            "offset": offset,
                            "media_key": media_key,
                            "is_video": is_video,
                            "is_audio": is_audio,
                        },
                    )
                    raw_duration = item.get("duration")
                    # Only images use duration. Treat 0/None as "missing" for images.
                    duration = raw_duration if (raw_duration is not None and int(raw_duration) >= 1) else default_duration
                    muted = self._effective_playback_muted(
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
                        load_cmd = self._preloaded_load_cmd

                    # Only tune mpv for the first preloaded item; before the next loadfile replace
                    # these extra set_property calls just queue behind a busy IPC socket.
                    if skip_load and not is_preloaded_network:
                        try:
                            self._mpv_manager._send_command(
                                {"command": ["set_property", "loop-file", "no"]},
                                timeout=5.0,
                            )
                        except Exception:
                            pass
                        if is_video:
                            try:
                                self._mpv_manager._send_command(
                                    {"command": ["set_property", "keep-open", "no"]},
                                    timeout=3.0,
                                )
                            except Exception:
                                pass

                    # Load next media file
                    load_started = time.monotonic()
                    # Respect per-media backoff to avoid tight loops on failing streams.
                    try:
                        b = self._media_backoff.get(media_key) or {}
                        next_try = float(b.get("next_try_monotonic") or 0.0)
                    except Exception:
                        next_try = 0.0
                    if next_try and time.monotonic() < next_try:
                        wait_sec = max(0.0, next_try - time.monotonic())
                        self.logger.debug(
                            "Backoff active for media",
                            extra={"media_key": media_key, "wait_sec": round(wait_sec, 2)},
                        )
                        self._stop_event.wait(timeout=min(30.0, wait_sec))
                        if self._stop_event.is_set() or self._active_playlist_id != playlist_id:
                            break
                    if not skip_load:
                        if not self._refresh_item_playback_path(item):
                            self.logger.warning(
                                "Failed to refresh external media for playlist loop",
                                extra={"media_key": str(item.get("key") or ""), "playlist_id": playlist_id},
                            )
                            continue
                        path = item["path"]
                    self.logger.debug(
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
                        normalized_headers = self._sanitize_headers_for_mpv(
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
                                self._mpv_manager.set_playback_stream_opening(True)
                            except Exception:
                                pass
                            self._prepare_mpv_network_reload()
                        # External streams: Referer/UA must be set before loadfile (and cleared between items).
                        normalized_headers, mpv_per_file_opts = self._apply_mpv_http_headers(item, stream_url=str(path))
                        self._apply_mpv_ytdl_options(item, stream_url=str(path))
                        if is_network_reload:
                            load_cmd = self._mpv_loadfile_command(
                                str(path),
                                "replace",
                                per_file_opts=mpv_per_file_opts,
                            )
                            load_timeout = self._network_loadfile_timeout_sec(
                                str(path), is_network=is_network_reload
                            )
                            load_timeout = max(5.0, min(180.0, float(load_timeout)))
                            load_resp = self._issue_loadfile(
                                load_cmd,
                                media_key=media_key,
                                timeout=load_timeout,
                            )
                            load_ok = bool(load_resp and load_resp.get("error") == "success")
                        elif is_audio:
                            self._prepare_mpv_audio_before_loadfile()
                            try:
                                audio_opts = self._logo_manager.prepare_audio_playback()
                            except Exception:
                                audio_opts = {"vid": "no", "keep-open": "no"}
                            merged_opts = dict(mpv_per_file_opts or {})
                            merged_opts.update(audio_opts)
                            load_ok = self._safe_loadfile(
                                str(path),
                                media_key=media_key,
                                is_video=False,
                                is_audio=True,
                                per_file_opts=merged_opts,
                                timeout=10.0,
                                wait_vo=False,
                            )
                            if not load_ok:
                                self._logo_manager.restore_after_audio_playback()
                                self._brief_idle_logo_on_skip()
                                continue
                        else:
                            self._prepare_mpv_audio_before_loadfile()
                            load_ok = self._safe_loadfile(
                                str(path),
                                media_key=media_key,
                                is_video=is_video,
                                per_file_opts=mpv_per_file_opts,
                                timeout=10.0,
                            )
                            if not load_ok:
                                self._brief_idle_logo_on_skip()
                                continue
                        socket_missing = not os.path.exists(PlaybackConstants.SOCKET_PATH)
                        if not load_ok and not is_network_reload:
                            self.logger.warning(
                                "MPV loadfile failed",
                                extra={
                                    "path": path,
                                    "mpv_response": load_resp,
                                    "socket_missing": socket_missing,
                                },
                            )
                            self._register_media_failure(
                                media_key,
                                reason="socket_missing" if socket_missing else "loadfile_failed",
                            )
                            if socket_missing:
                                self._stop_event.wait(timeout=5.0)
                            continue
                        if not load_ok and is_network_reload:
                            self.logger.info(
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
                            if skip_load and is_preloaded_network:
                                self._record_ytdl_open_success()
                                self._set_last_good_playback(
                                    playlist_id,
                                    item_index,
                                    media_key,
                                    len(items),
                                )
                            cycle_network_attempted += 1
                            if not self._ensure_network_stream_started(
                                item,
                                str(path),
                                normalized_headers=normalized_headers or None,
                                load_cmd=load_cmd,
                                load_ipc_ok=bool(load_ok),
                            ):
                                if self._stop_event.is_set():
                                    break
                                try:
                                    self._mpv_manager.set_playback_stream_opening(False)
                                except Exception:
                                    pass
                                self.logger.warning(
                                    "MPV network stream open failed",
                                    extra={
                                        "path": str(path)[:120],
                                        "load_ipc_ok": bool(load_ok),
                                        "media_key": media_key,
                                    },
                                )
                                self._register_media_failure(
                                    media_key,
                                    reason="socket_missing" if socket_missing else "open_failed",
                                )
                                if is_network:
                                    cycle_network_failed += 1
                                    self._record_ytdl_open_failure(
                                        media_key=media_key,
                                        reason="open_failed",
                                    )
                                    streak = int(self._consecutive_ytdl_failures or 0)
                                    if streak >= net_open_abort:
                                        self.logger.warning(
                                            "Aborting playlist scan after consecutive network open failures",
                                            extra={
                                                "playlist_id": playlist_id,
                                                "consecutive_ytdl_failures": streak,
                                                "abort_threshold": net_open_abort,
                                                "cooldown_sec": round(net_open_cooldown_sec, 1),
                                                "resume_index": item_index,
                                                "last_good_media_key": self._last_good_media_key,
                                            },
                                        )
                                        resume_at = self.get_resume_start_index_for_hung_recovery()
                                        start_index = resume_at
                                        self._stop_event.wait(timeout=net_open_cooldown_sec)
                                        net_open_cycle_abort = True
                                        break
                                if socket_missing:
                                    self._stop_event.wait(timeout=5.0)
                                continue
                            stream_ready = True
                            self._apply_post_loadfile_playback_props(
                                muted=muted,
                                item_muted=bool(item.get("muted", False)),
                                profile_muted=profile_muted,
                            )
                            if is_network:
                                self._record_ytdl_open_success()
                                self._set_last_good_playback(
                                    playlist_id,
                                    item_index,
                                    media_key,
                                    len(items),
                                )
                        elif not skip_load:
                            self._apply_post_loadfile_playback_props(
                                muted=muted,
                                item_muted=bool(item.get("muted", False)),
                                profile_muted=profile_muted,
                            )
                        self._schedule_content_cache_prefetch(items, item_index)
                        if not self._wait_mpv_video_end(
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
                    elif is_audio:
                        try:
                            self._mpv_manager.set_playback_local_audio_active(True)
                        except Exception:
                            pass
                        try:
                            if not self._prepare_local_audio_after_loadfile(
                                muted=muted,
                                skip_load=skip_load,
                                item_muted=bool(item.get("muted", False)),
                                profile_muted=profile_muted,
                            ):
                                if not skip_load:
                                    try:
                                        self._logo_manager.restore_after_audio_playback()
                                    except Exception:
                                        pass
                                    self._brief_idle_logo_on_skip()
                                continue
                            if not self._wait_mpv_video_end(
                                playlist_id,
                                is_network=False,
                                stream_ready=True,
                                poll_sec=2.5,
                                media_key=media_key,
                                is_audio=True,
                            ):
                                stall_abort = True
                                try:
                                    self._logo_manager.restore_after_audio_playback()
                                except Exception:
                                    pass
                                break
                            try:
                                self._logo_manager.restore_after_audio_playback()
                            except Exception:
                                pass
                        finally:
                            try:
                                self._mpv_manager.set_playback_local_audio_active(False)
                            except Exception:
                                pass
                    else:
                        # For still images, keep the frame open to avoid quick close/reopen churn.
                        # (Videos should not be kept open; they are EOF-driven here.)
                        try:
                            self._mpv_manager._send_command(
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
                        loaded = self._wait_for_mpv_loaded_path(path, timeout=15.0)
                        vo_ready = self._wait_for_mpv_vo_configured(timeout=5.0)
                        ready_at = time.monotonic()
                        load_wait_sec = round(ready_at - load_started, 3)

                        dur_sec = max(1, int(duration))
                        timer_mode = os.getenv("DSIGN_IMAGE_TIMER_MODE", "from_load").strip().lower()
                        if timer_mode not in ("from_load", "from_ready"):
                            timer_mode = "from_load"
                        base_t = load_started if timer_mode == "from_load" else ready_at
                        switch_at = base_t + dur_sec
                        self.logger.debug(
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
                        reached = self._sleep_until(switch_at, step=0.2)
                        if reached:
                            fired_at = time.monotonic()
                            drift_sec = round(fired_at - (base_t + dur_sec), 3)
                            # Positive drift means we switched later than scheduled (thread wake / load spikes).
                            self.logger.debug(
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
                    next_start = self._handle_all_network_items_failed_cycle(
                        playlist_id=playlist_id,
                        items_count=len(items),
                    )
                    if next_start is not None:
                        start_index = int(next_start)
                    continue

                if stall_abort or self._stall_restart_was_requested():
                    self.logger.info(
                        "Slideshow loop paused for mpv stall recovery",
                        extra={
                            "playlist_id": playlist_id,
                            "cycle": loop_cycle,
                            "loop_item_index": self._loop_item_index,
                        },
                    )
                    break

        finally:
            try:
                self._mpv_manager.set_playback_local_audio_active(False)
            except Exception:
                pass
            try:
                if self._logo_manager.ensure_mpv_video_output():
                    pass
                else:
                    self._sync_settings_volume_to_mpv()
            except Exception:
                pass
            self._mpv_manager.set_playback_session_active(False)
    def _register_media_failure(self, media_key: str, reason: str = "unknown") -> None:
        """Exponential backoff for media that fails to start/open."""
        if not media_key:
            return
        entry = self._media_backoff.get(media_key) or {"failures": 0, "next_try_monotonic": 0.0}
        try:
            failures = int(entry.get("failures") or 0) + 1
        except Exception:
            failures = 1
        # 2s, 4s, 8s... cap at 120s, add small jitter.
        delay = min(120.0, float(2 ** min(failures, 6)))
        jitter = min(1.5, 0.1 * delay)
        next_try = time.monotonic() + delay + (jitter * (0.5))
        entry["failures"] = failures
        entry["next_try_monotonic"] = next_try
        self._media_backoff[media_key] = entry
        self.logger.warning(
            "Media backoff scheduled",
            extra={
                "media_key": media_key,
                "reason": reason,
                "failures": failures,
                "delay_sec": round(delay, 2),
            },
        )

    def _reset_media_backoff(self, media_key: str) -> None:
        if not media_key:
            return
        if media_key in self._media_backoff:
            try:
                del self._media_backoff[media_key]
            except Exception:
                self._media_backoff.pop(media_key, None)

    def play(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
    ) -> bool:
        """Play playlist with profile support"""
        with self._app_context():
            return self._play_impl(
                playlist_id,
                start_index=start_index,
                preserve_stall_tracking=preserve_stall_tracking,
            )

    def _play_impl(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
    ) -> bool:
        from ..models import PlaybackStatus, Playlist, PlaylistProfileAssignment, PlaybackProfile

        try:
            # Stop any previous manual playback loop
            self._stop_play_thread(preserve_stall_tracking=preserve_stall_tracking)
            # Mark playback starting before DB/profile IPC so Wi-Fi-on-display skips.
            self._set_playback_active_marker(True)
            self._audio_route_applied_for_play = False

            # Get playlist and validate
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            # Get assigned profile if exists
            profile_settings = {}
            assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=playlist_id
            ).first()
        
            if assignment and assignment.profile_id:
                profile = self.db_session.query(PlaybackProfile).get(assignment.profile_id)
                if profile:
                    # `settings` is stored as JSON in DB, so it is already a dict.
                    profile_settings = profile.settings or {}

            profile_muted = bool(profile_settings.get("mute", False))

            # Apply profile settings first
            if profile_settings:
                if not self._mpv_manager.update_settings(profile_settings):
                    self.logger.warning("Failed to apply some profile settings")

            # panscan>0 даёт «зум под размер экрана» и на части DRM/сборок ведёт к обрезке даже при 16:9.
            # По умолчанию вписываем кадр без обрезки; при необходимости убрать полосы — задать panscan в профиле MPV.
            if "panscan" not in profile_settings:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "panscan", 0.0]},
                    timeout=2.0,
                )

            # Manual playback loop is the most reliable way to enforce per-item durations on mpv builds
            # where ffconcat timing is inconsistent for images and mixed media.
            items = []
            missing = []
            # Enforce stable playback order (PlaylistFiles.order in DB).
            files = sorted((playlist.files or []), key=lambda x: int(getattr(x, "order", 0) or 0))
            for pf in files:
                resolved = self._resolve_playlist_item_path(getattr(pf, "file_name", None))
                if not resolved or not resolved.get("path"):
                    missing.append(str(getattr(pf, "file_name", "")))
                    continue

                is_video = bool(resolved.get("is_video"))
                is_audio = bool(resolved.get("is_audio"))
                file_name = str(getattr(pf, "file_name", "") or "")
                items.append(
                    {
                        "key": resolved.get("key") or file_name,
                        "label": self._media_label_for_file_name(file_name),
                        "path": resolved["path"],
                        "duration": int(getattr(pf, "duration", 0) or 0),
                        "is_video": is_video,
                        "is_audio": is_audio,
                        "muted": bool(getattr(pf, "muted", False))
                        if (is_video or is_audio)
                        else False,
                        "http_headers": resolved.get("http_headers") or {},
                        "page_url": resolved.get("page_url"),
                        "provider": resolved.get("provider"),
                    }
                )

            if not items:
                raise ValueError(
                    f"Playlist {playlist_id} has no existing media files"
                    + (f". Missing: {', '.join(missing[:10])}" if missing else "")
                    + (" ..." if len(missing) > 10 else "")
                )

            start_index = int(start_index or 0)
            if start_index < 0 or start_index >= len(items):
                start_index = 0

            playback_mode = self._playlist_playback_mode(items)
            if playback_mode in ("local_single", "local_playlist"):
                return self._play_local_video_engine(
                    playlist_id=playlist_id,
                    items=items,
                    start_index=start_index,
                    profile_muted=profile_muted,
                    profile_settings=profile_settings,
                    playlist=playlist,
                    mode=playback_mode,
                )

            self._active_playlist_id = playlist_id
            try:
                self._mpv_manager.set_playback_session_active(True)
            except Exception:
                pass
            self._set_loop_position(start_index, len(items))
            first = items[start_index]
            self._set_current_media_label(self._item_media_label(first))
            try:
                self._logo_manager.ensure_mpv_video_output()
            except Exception:
                pass

            first_path = str(first.get("path") or "")
            first_is_network = first_path.startswith(("http://", "https://", "ytdl://"))
            if first_is_network:
                try:
                    self._mpv_manager.set_playback_stream_opening(True)
                except Exception:
                    pass
            try:
                # Show first item immediately for responsiveness
                try:
                    # Do NOT loop the file at MPV level; the app controls looping.
                    self._mpv_manager._send_command(
                        {"command": ["set_property", "loop-file", "no"]},
                        timeout=2.0,
                    )
                except Exception:
                    pass
                _, first_mpv_opts = self._apply_mpv_http_headers(first, stream_url=first_path)
                self._apply_mpv_ytdl_options(first, stream_url=first_path)
                first_load_cmd = self._mpv_loadfile_command(
                    first_path,
                    "replace",
                    per_file_opts=first_mpv_opts,
                )
                first_load_timeout = (
                    self._ytdl_loadfile_ipc_timeout_sec()
                    if first_path.startswith("ytdl://")
                    else (45.0 if first_is_network else 10.0)
                )
                first_media_key = str(first.get("key") or first_path)
                # Network/ytdl opens in two phases (ytdl_hook resolve, then lavf reapply). Unpausing
                # here made playback start before lavf headers merged — visible "double start".
                if first_is_network:
                    try:
                        self._mpv_manager._send_command(
                            {"command": ["set_property", "pause", "yes"]},
                            timeout=3.0,
                        )
                    except Exception:
                        pass
                    first_load_resp = self._issue_loadfile(
                        first_load_cmd,
                        media_key=first_media_key,
                        timeout=first_load_timeout,
                        max_attempts=1,
                    )
                    self._preloaded_load_cmd = first_load_cmd
                else:
                    first_load_resp = None
                    loaded_local = False
                    for try_offset in range(len(items)):
                        try_index = (start_index + try_offset) % len(items)
                        candidate = items[try_index]
                        cand_path = str(candidate.get("path") or "")
                        if self._is_network_stream_path(cand_path):
                            continue
                        _, cand_mpv_opts = self._apply_mpv_http_headers(
                            candidate, stream_url=cand_path
                        )
                        cand_key = str(candidate.get("key") or cand_path)
                        cand_is_audio = bool(candidate.get("is_audio"))
                        if cand_is_audio:
                            self._prepare_mpv_audio_before_loadfile()
                            try:
                                audio_opts = self._logo_manager.prepare_audio_playback()
                            except Exception:
                                audio_opts = {"vid": "no", "keep-open": "no"}
                            merged_opts = dict(cand_mpv_opts or {})
                            merged_opts.update(audio_opts)
                            loaded_candidate = self._safe_loadfile(
                                cand_path,
                                media_key=cand_key,
                                is_video=False,
                                is_audio=True,
                                per_file_opts=merged_opts,
                                timeout=10.0,
                                wait_vo=False,
                            )
                        else:
                            self._prepare_mpv_audio_before_loadfile()
                            loaded_candidate = self._safe_loadfile(
                                cand_path,
                                media_key=cand_key,
                                is_video=bool(candidate.get("is_video")),
                                per_file_opts=cand_mpv_opts,
                                timeout=10.0,
                            )
                        if loaded_candidate:
                            if try_index != start_index:
                                start_index = try_index
                                first = candidate
                                first_path = cand_path
                                first_media_key = cand_key
                                self._set_current_media_label(self._item_media_label(first))
                                self._set_loop_position(start_index, len(items))
                            loaded_local = True
                            first_load_resp = {"error": "success"}
                            break
                    if not loaded_local:
                        raise RuntimeError("No playable local media at playlist start")
                    first_muted = self._effective_playback_muted(
                        item_muted=bool(first.get("muted", False)),
                        profile_muted=profile_muted,
                    )
                    self._apply_post_loadfile_playback_props(
                        muted=first_muted,
                        item_muted=bool(first.get("muted", False)),
                        profile_muted=profile_muted,
                    )
            except Exception:
                if first_is_network:
                    try:
                        self._mpv_manager.set_playback_stream_opening(False)
                    except Exception:
                        pass
                raise

            self._preloaded_stream_ready = False
            if first_is_network and bool(first.get("is_video")):
                self.logger.info(
                    "Playback play: network loadfile issued (unpause deferred until stream ready)",
                    extra={
                        "playlist_id": playlist_id,
                        "media_key": str(first.get("key") or first_path),
                        "load_ok": bool(first_load_resp and first_load_resp.get("error") == "success"),
                        "path_preview": first_path[:120],
                        "deferred_unpause": True,
                    },
                )

            # Update playback status (single-row table; keep id=1 stable)
            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            playback.playlist_id = playlist_id
            playback.status = 'playing'
            self.db_session.add(playback)
            self.db_session.commit()

            # Start background loop to enforce durations and EOF waits.
            # play() loadfile'd items[start_index]; loop walks from there, skips reload on first offset once.
            self._play_thread = Thread(
                target=self._run_manual_slideshow_loop,
                args=(playlist_id, items, start_index),
                kwargs={
                    "first_item_preloaded": True,
                    "profile_muted": profile_muted,
                },
                daemon=True,
            )
            self._play_thread.start()

            # Notify clients
            try:
                if self.socketio:
                    self.socketio.emit(
                        'playback_update',
                        {
                            'status': 'playing',
                            'playlist_id': playlist.id,
                            'current_media': self._get_current_media_label(),
                            'playlist': {'id': playlist.id, 'name': playlist.name},
                            'settings': profile_settings,
                        },
                    )
            except Exception:
                # Best-effort: playback must continue even if sockets are unavailable.
                pass
        
            return True

        except Exception as e:
            self.logger.error(
                "Playback error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
            try:
                self.db_session.rollback()
            except Exception:
                pass

            # Best-effort: persist non-playing state so UI doesn't show green when we fell back to idle
            try:
                from ..models import PlaybackStatus
                playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
                playback.status = 'idle'
                playback.playlist_id = None
                self.db_session.add(playback)
                self.db_session.commit()
            except Exception:
                try:
                    self.db_session.rollback()
                except Exception:
                    pass

            self._set_playback_active_marker(False)
            try:
                self._mpv_manager.set_playback_session_active(False)
            except Exception:
                pass

            # Fall back to idle logo
            try:
                self._logo_manager.ensure_mpv_video_output()
            except Exception:
                pass
            self._logo_manager.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(
        self,
        *,
        show_idle_logo: bool = True,
        update_status: bool = True,
        preserve_stall_tracking: bool = False,
        preserve_loop_position: bool = False,
    ) -> bool:
        """Stop playback and persist stopped state so UI/API match MPV (idle logo)."""
        with self._app_context():
            return self._stop_impl(
                show_idle_logo=show_idle_logo,
                update_status=update_status,
                preserve_stall_tracking=preserve_stall_tracking,
                preserve_loop_position=preserve_loop_position,
            )

    def _stop_impl(
        self,
        *,
        show_idle_logo: bool = True,
        update_status: bool = True,
        preserve_stall_tracking: bool = False,
        preserve_loop_position: bool = False,
    ) -> bool:
        from ..models import PlaybackStatus

        try:
            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            last_playlist_id = playback.playlist_id

            self._stop_play_thread(
                preserve_stall_tracking=preserve_stall_tracking,
                preserve_loop_position=preserve_loop_position,
            )
            try:
                self._logo_manager.ensure_mpv_video_output()
            except Exception:
                pass
            self._set_playback_active_marker(False)
            ok = True
            if show_idle_logo:
                ok = self._logo_manager.display_idle_logo()

            if update_status:
                playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
                playback.status = "stopped"
                playback.playlist_id = last_playlist_id
                self.db_session.add(playback)
                self.db_session.commit()

                try:
                    if self.socketio:
                        self.socketio.emit(
                            'playback_update',
                            {
                                'status': 'stopped',
                                'playlist_id': last_playlist_id,
                                'current_media': None,
                            },
                        )
                except Exception:
                    pass
            return ok
        except Exception as e:
            self.logger.error(
                "Stop error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
            return False

    def _get_loop_position_snapshot(self) -> tuple[Optional[int], int]:
        with self._loop_position_lock:
            return self._loop_item_index, self._loop_items_count

    def _get_mpv_playback_snapshot(self) -> Dict[str, Any]:
        """Light MPV IPC snapshot for B1 status (short TTL cache)."""
        try:
            ttl = float((os.getenv("DSIGN_PLAYBACK_STATUS_IPC_TTL_SEC") or "2").strip())
        except ValueError:
            ttl = 2.0
        ttl = max(0.0, min(15.0, ttl))
        now = time.monotonic()
        if ttl > 0 and (now - self._status_snapshot_ts) < ttl:
            return dict(self._status_snapshot_cache)

        snapshot: Dict[str, Any] = {
            "time_pos": None,
            "duration": None,
            "is_network": False,
            "mpv_responsive": False,
        }
        try:
            health = self._mpv_manager.check_health()
            snapshot["mpv_responsive"] = bool(health.get("responsive"))
            if not snapshot["mpv_responsive"]:
                self._status_snapshot_cache = snapshot
                self._status_snapshot_ts = now
                return dict(snapshot)

            tp = self._mpv_manager.get_property_light("time-pos", timeout=1.5)
            dur = self._mpv_manager.get_property_light("duration", timeout=1.5)
            path_raw = self._mpv_manager.get_property_light("path", timeout=1.0)
            if tp is not None:
                try:
                    snapshot["time_pos"] = float(tp)
                except (TypeError, ValueError):
                    pass
            if dur is not None:
                try:
                    snapshot["duration"] = float(dur)
                except (TypeError, ValueError):
                    pass
            if path_raw:
                snapshot["is_network"] = self._is_network_stream_path(path_raw)
        except Exception:
            pass

        self._status_snapshot_cache = snapshot
        self._status_snapshot_ts = now
        return dict(snapshot)

    def get_status(self) -> Dict:
        """Get current playback status"""
        from ..models import PlaybackStatus
        
        status = self.db_session.query(PlaybackStatus).get(1) or self.db_session.query(PlaybackStatus).first()
        cache_state: Dict[str, Any] = {}
        if self._content_cache is not None:
            try:
                cache_state = self._content_cache.get_status_summary()
            except Exception:
                cache_state = {}

        item_index, item_count = self._get_loop_position_snapshot()
        media_key = self._last_loaded_media_key
        mpv_snap = self._get_mpv_playback_snapshot()

        return {
            'status': status.status if status else None,
            'playlist_id': status.playlist_id if status else None,
            'item_index': item_index,
            'item_count': item_count,
            'media_key': media_key,
            'time_pos': mpv_snap.get("time_pos"),
            'duration': mpv_snap.get("duration"),
            'is_network': bool(mpv_snap.get("is_network")),
            'mpv_responsive': bool(mpv_snap.get("mpv_responsive")),
            'current_media': self._get_current_media_label(),
            'settings': self._mpv_manager._current_settings,
            'network_health': self.get_network_playback_health(),
            'cache_state': cache_state,
        }

    def get_playback_info(self) -> Dict:
        """Get current playback info"""
        info = {}
        for category, settings in self._mpv_manager._current_settings.items():
            info[category] = {}
            for setting in settings.keys():
                response = self._mpv_manager._send_command({
                    "command": ["get_property", setting]
                })
                if response and 'data' in response:
                    info[category][setting] = response['data']
        return info
        
    def stop_idle_logo(self):
        """Stop idle logo display"""
        try:
            res = self._mpv_manager._send_command({"command": ["stop"]})
            if res is not None:
                self.logger.info("Idle logo stopped")
            else:
                self.logger.warning("Failed to get confirmation of idle logo stop")
        except Exception as e:
            self.logger.error(f"Failed to stop idle logo: {str(e)}")
            
    def restart_idle_logo(self) -> bool:
        """Restart idle logo display"""
        try:
            self.stop_idle_logo()
            return self._logo_manager.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Failed to restart idle logo: {str(e)}")
            return False
