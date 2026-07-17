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
from .playback_eof import PlaybackEofWaiter, is_external_stream_provider
from .playback_network import PlaybackNetworkHelper
from .playback_slideshow import PlaybackSlideshowLoop
from .playback_play import PlaybackPlayRunner

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
        # key -> {failures:int, next_try_monotonic:float, last_touch_monotonic:float}
        self._media_backoff: Dict[str, Dict[str, Any]] = {}
        self._app = None
        self._preloaded_stream_ready = False
        self._preloaded_load_cmd: Optional[List[Any]] = None
        self._preloaded_load_ipc_ok: bool = True
        self._current_media_label: Optional[str] = None
        self._current_media_lock = Lock()
        self._loop_item_index: Optional[int] = None
        self._loop_items_count: int = 0
        self._loop_position_lock = Lock()
        self._override_lock = Lock()
        # Bumps on stop so an in-flight play() loadfile cannot resurrect status=playing.
        self._play_seq = 0
        self._play_seq_lock = Lock()
        # Serializes Stop vs play() final commit (thread start + DB persist) so a late
        # schedule play cannot overwrite Stop after return-to-schedule.
        self._control_lock = Lock()
        self._override_return_ctx: Optional[Dict[str, Any]] = None
        self._playlist_single_pass: bool = False
        self._on_override_return: Optional[Callable[[], None]] = None
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
        self._playback_current_media_key: Optional[str] = None
        self._audio_route_applied_for_play = False
        self._item_skip_lock = Lock()
        self._item_skip_event = Event()
        self._playback_eof = PlaybackEofWaiter(self)
        self._playback_network = PlaybackNetworkHelper(self)
        self._playback_slideshow = PlaybackSlideshowLoop(self)
        self._playback_play = PlaybackPlayRunner(self)
        self._item_skip_direction = "next"
        self._active_playback_mode: Optional[str] = None
        # Invalidates in-flight idle-logo retries so a late Stop retry cannot kill Play.
        self._idle_logo_epoch = 0
        self._idle_logo_epoch_lock = Lock()
        self._play_start_mono: float = 0.0
        # One play()/loadfile at a time — parallel schedule+manual starts race to idle.
        self._play_start_lock = Lock()
        # After ScheduleEngine attach / service restart — desync watch must not
        # clear or re-stop a boot resume that is still settling.
        self._boot_grace_until: float = 0.0
        # Invalidates in-flight ytdl ensure() after Stop join timeout (orphan thread).
        self._playback_run_id: int = 0
        self._orphan_play_threads: List[Any] = []

    def begin_boot_grace(self, seconds: float = 60.0) -> None:
        self._boot_grace_until = time.monotonic() + max(0.0, float(seconds))

    def in_boot_grace(self) -> bool:
        return time.monotonic() < float(self._boot_grace_until or 0.0)

    def _bump_playback_run_id(self) -> int:
        self._playback_run_id = int(self._playback_run_id) + 1
        return int(self._playback_run_id)

    def _is_playback_run_current(self, run_id: int) -> bool:
        try:
            return int(run_id) == int(self._playback_run_id)
        except Exception:
            return False

    def _bump_idle_logo_epoch(self) -> int:
        with self._idle_logo_epoch_lock:
            self._idle_logo_epoch = int(self._idle_logo_epoch) + 1
            return int(self._idle_logo_epoch)

    def _idle_logo_epoch_current(self) -> int:
        with self._idle_logo_epoch_lock:
            return int(self._idle_logo_epoch)

    def mark_play_starting(self) -> None:
        """Call when Play/enqueue_play begins — cancel logo retries and soft-stale window.

        Does **not** bump ``_play_seq`` (that is ``invalidate_in_flight_play`` /
        ``_begin_play_seq``). Confusing those two caused false "double bump" audits.

        Also resets ytdl open-failure streak / media backoff so Stop→Play and
        return-to-schedule do not inherit fail-fast budgets that skip VK/Rutube.
        """
        self._play_start_mono = time.monotonic()
        self._bump_idle_logo_epoch()
        self.reset_network_open_health()

    def reset_network_open_health(self) -> None:
        """Clear open-failure counters so a fresh Play gets a full ytdl budget."""
        with self._ytdl_health_lock:
            self._consecutive_ytdl_failures = 0
        try:
            self._media_backoff.clear()
        except Exception:
            self._media_backoff = {}

    def invalidate_in_flight_play(self) -> None:
        """Abort any in-flight play() so it cannot commit after Stop / return-to-schedule.

        Must run on the HTTP/caller thread *before* claim_playback_intent or schedule
        plan enqueue — otherwise a loadfile still holding ``_play_start_lock`` can
        finish and re-persist ``source=manual`` over the new intent.

        Does **not** bump ``_playback_run_id`` — that races async Stop cleanup against
        a concurrent Play (force-restart mpv after ytdl loadfile). Run id is bumped in
        ``_stop_play_thread`` / ``enqueue_play`` instead.
        """
        self.mark_play_starting()
        self._bump_play_seq()
        try:
            self._stop_event.set()
        except Exception:
            pass
        try:
            self._mpv_manager.set_playback_stream_opening(False)
        except Exception:
            pass

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

    def _release_db_session(self) -> None:
        """Return scoped-session connection before long mpv/ytdl waits (pool exhaustion)."""
        try:
            remove = getattr(self.db_session, "remove", None)
            if callable(remove):
                remove()
                return
        except Exception:
            pass
        try:
            from ..extensions import db

            db.session.remove()
        except Exception:
            pass

    def _begin_play_seq(self) -> int:
        with self._play_seq_lock:
            self._play_seq = int(self._play_seq) + 1
            return int(self._play_seq)

    def _bump_play_seq(self) -> None:
        """Invalidate in-flight play() so it will not persist playing after Stop."""
        with self._play_seq_lock:
            self._play_seq = int(self._play_seq) + 1

    def _is_play_seq_current(self, seq: int) -> bool:
        with self._play_seq_lock:
            return int(seq) == int(self._play_seq)

    def _commit_play(
        self,
        play_seq: int,
        *,
        start_thread: Callable[[], None],
        persist: Callable[[], None],
        on_abort: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Atomically finish play against Stop: thread + persist under control lock."""
        with self._control_lock:
            if not self._is_play_seq_current(play_seq):
                if on_abort is not None:
                    try:
                        on_abort()
                    except Exception:
                        pass
                return False
            start_thread()
            if not self._is_play_seq_current(play_seq):
                # Stop won between start and persist — tear down without writing playing.
                try:
                    if self._play_thread and self._play_thread.is_alive():
                        self._stop_event.set()
                        self._play_thread.join(timeout=1.0)
                except Exception:
                    pass
                self._play_thread = None
                if on_abort is not None:
                    try:
                        on_abort()
                    except Exception:
                        pass
                return False
            persist()
            if not self._is_play_seq_current(play_seq):
                # Persist raced under lock should be rare; Stop holds same lock so this
                # path means seq was bumped elsewhere — re-stop without releasing playing.
                try:
                    if self._play_thread and self._play_thread.is_alive():
                        self._stop_event.set()
                        self._play_thread.join(timeout=1.0)
                except Exception:
                    pass
                self._play_thread = None
                if on_abort is not None:
                    try:
                        on_abort()
                    except Exception:
                        pass
                return False
            return True

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

    def set_override_return_handler(self, handler: Optional[Callable[[], None]]) -> None:
        """Schedule engine hook after override single-pass ends (wired in D2.2)."""
        self._on_override_return = handler

    def _persist_playback_status(
        self,
        *,
        playlist_id: Optional[int],
        status: str,
        source: Optional[str] = None,
        rule_id: Optional[int] = None,
        clear_rule: bool = False,
    ) -> None:
        from ..models import PlaybackStatus

        playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
        playback.playlist_id = playlist_id
        playback.status = status
        if source is not None:
            playback.source = source
        if clear_rule:
            playback.rule_id = None
        elif rule_id is not None:
            playback.rule_id = rule_id
        self.db_session.add(playback)
        self.db_session.commit()

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
            with self._playback_mute_lock:
                extra["item_muted"] = self._playback_item_muted
                extra["profile_muted"] = self._playback_profile_muted
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

    def _fresh_playback_mute_flags(self) -> tuple[bool, bool]:
        """Current per-file/profile mute flags (refresh per-file from DB when possible)."""
        with self._playback_mute_lock:
            profile_muted = self._playback_profile_muted
            key = self._playback_current_media_key
            cached_item = self._playback_item_muted
        item_muted = cached_item
        pid = self._active_playlist_id
        if pid and key:
            try:
                from ..models import PlaylistFiles

                with self._app_context():
                    row = (
                        self.db_session.query(PlaylistFiles)
                        .filter_by(playlist_id=int(pid), file_name=str(key))
                        .first()
                    )
                    if row is not None:
                        item_muted = bool(getattr(row, "muted", False))
            except Exception:
                item_muted = cached_item
        with self._playback_mute_lock:
            self._playback_item_muted = bool(item_muted)
        return bool(item_muted), bool(profile_muted)

    def _maybe_clear_global_mute_on_item_start(
        self, *, item_muted: bool, profile_muted: bool
    ) -> None:
        """Global dashboard mute is per-item; unmuted playlist files should not inherit it."""
        if item_muted or profile_muted:
            return
        svc = self._settings_service
        if not svc:
            return
        try:
            st = svc.load_settings()
            if not bool(st.get("mute", False)):
                return
            svc.set_master_audio(muted=False)
        except Exception:
            pass

    def try_clear_global_mute_on_volume(self, volume_percent: int | None) -> None:
        """Volume knob clears global mute only (not per-file playlist mute)."""
        if volume_percent is None or int(volume_percent) <= 0:
            return
        item_muted, profile_muted = self._fresh_playback_mute_flags()
        if item_muted or profile_muted:
            return
        svc = self._settings_service
        if not svc:
            return
        try:
            st = svc.load_settings()
            if not bool(st.get("mute", False)):
                return
            svc.set_master_audio(muted=False)
        except Exception:
            pass

    def reapply_effective_mute_to_mpv(self) -> None:
        """Re-apply playlist/profile/global mute after volume API may have cleared mpv mute."""
        item_muted, profile_muted = self._fresh_playback_mute_flags()
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

    def _refresh_item_mute_from_db(self, playlist_id: int, item: Dict[str, Any]) -> None:
        """Re-read per-file muted from DB (playlist editor may save while playback runs)."""
        key = str(item.get("key") or "").strip()
        if not key:
            return
        try:
            from ..models import PlaylistFiles

            with self._app_context():
                row = (
                    self.db_session.query(PlaylistFiles)
                    .filter_by(playlist_id=int(playlist_id), file_name=key)
                    .first()
                )
                if row is not None:
                    item["muted"] = bool(getattr(row, "muted", False))
        except Exception:
            pass

    def _on_playlist_item_start(
        self,
        playlist_id: int,
        item: Dict[str, Any],
        *,
        profile_muted: bool,
    ) -> None:
        """Shared hook when a playlist item becomes active."""
        self._refresh_item_mute_from_db(playlist_id, item)
        media_key = str(item.get("key") or item.get("path") or "")
        with self._playback_mute_lock:
            self._playback_current_media_key = media_key or None
        item_muted_flag = bool(item.get("muted", False))
        self._set_playback_mute_context(
            item_muted=item_muted_flag,
            profile_muted=profile_muted,
        )
        self._maybe_clear_global_mute_on_item_start(
            item_muted=item_muted_flag,
            profile_muted=profile_muted,
        )
        self._publish_current_media(playlist_id, item)

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

    def _cancel_content_cache_prefetches(self) -> None:
        cache = self._content_cache
        if cache is None or not hasattr(cache, "cancel_prefetches"):
            return
        try:
            cache.cancel_prefetches()
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
        # No recoverable item: idle immediately. A 5-minute cooldown left the UI on
        # "playing" while mpv showed nothing (VK/Rutube open failures).
        try:
            self._prepare_mpv_for_new_play(lock_wait=1.0)
        except Exception:
            pass
        try:
            self._logo_manager.display_idle_logo(lock_wait=2.0)
        except Exception:
            pass
        try:
            self._persist_playback_status(
                playlist_id=None,
                status="idle",
                source="idle",
                clear_rule=True,
            )
        except Exception:
            try:
                self.db_session.rollback()
            except Exception:
                pass
        try:
            if self.socketio:
                self.socketio.emit(
                    "playback_update",
                    {
                        "status": "idle",
                        "playlist_id": None,
                        "source": "idle",
                        "current_media": None,
                    },
                )
        except Exception:
            pass
        self.logger.warning(
            "playlist: no last-good media after network failure cycle; idle",
            extra={
                "playlist_id": playlist_id,
                "consecutive_ytdl_failures": health.get("consecutive_ytdl_failures"),
            },
        )
        self._stop_event.set()
        return None

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
            # Wayland: vo-configured often lags after idle/stop; loadfile success is enough.
            if PlaybackConstants.is_wayland_backend():
                return True
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
            if self._stop_event.is_set() or self._item_skip_event.is_set():
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





    @staticmethod
    def _float_env(name: str, default: float, *, lo: float, hi: float) -> float:
        try:
            v = float((os.getenv(name) or str(default)).strip())
        except ValueError:
            v = float(default)
        return max(lo, min(hi, v))


    def _apply_mpv_http_headers(self, *args, **kwargs):
        return self._playback_network._apply_mpv_http_headers(*args, **kwargs)

    def _apply_mpv_lavf_headers_after_ytdl_hook(self, *args, **kwargs):
        return self._playback_network._apply_mpv_lavf_headers_after_ytdl_hook(*args, **kwargs)

    def _apply_mpv_ytdl_options(self, *args, **kwargs):
        return self._playback_network._apply_mpv_ytdl_options(*args, **kwargs)

    def _build_mpv_stream_lavf_o_opts(self, *args, **kwargs):
        return self._playback_network._build_mpv_stream_lavf_o_opts(*args, **kwargs)

    def _clear_mpv_http_options(self, *args, **kwargs):
        return self._playback_network._clear_mpv_http_options(*args, **kwargs)

    def _detect_mpv_instant_eof(self, *args, **kwargs):
        return self._playback_network._detect_mpv_instant_eof(*args, **kwargs)

    def _ensure_network_stream_started(self, *args, **kwargs):
        return self._playback_network._ensure_network_stream_started(*args, **kwargs)

    def _ensure_network_stream_started_impl(self, *args, **kwargs):
        return self._playback_network._ensure_network_stream_started_impl(*args, **kwargs)

    def _escape_mpv_key_value_list_token(self, *args, **kwargs):
        return self._playback_network._escape_mpv_key_value_list_token(*args, **kwargs)

    def _format_mpv_key_value_list(self, *args, **kwargs):
        return self._playback_network._format_mpv_key_value_list(*args, **kwargs)

    def _is_valid_lavf_option_key(self, *args, **kwargs):
        return self._playback_network._is_valid_lavf_option_key(*args, **kwargs)

    def _lavf_network_timeout_opts(self, *args, **kwargs):
        return self._playback_network._lavf_network_timeout_opts(*args, **kwargs)

    def _log_mpv_network_debug_snapshot(self, *args, **kwargs):
        return self._playback_network._log_mpv_network_debug_snapshot(*args, **kwargs)

    def _merge_mpv_lavf_options(self, *args, **kwargs):
        return self._playback_network._merge_mpv_lavf_options(*args, **kwargs)

    def _normalize_mpv_http_headers(self, *args, **kwargs):
        return self._playback_network._normalize_mpv_http_headers(*args, **kwargs)

    def _sanitize_headers_for_mpv(self, *args, **kwargs):
        return self._playback_network._sanitize_headers_for_mpv(*args, **kwargs)

    def _strip_accept_language_header_lines(self, *args, **kwargs):
        return self._playback_network._strip_accept_language_header_lines(*args, **kwargs)

    def _try_midstream_network_reload(self, *args, **kwargs):
        return self._playback_network._try_midstream_network_reload(*args, **kwargs)

    def _wait_mpv_leave_idle(self, *args, **kwargs):
        return self._playback_network._wait_mpv_leave_idle(*args, **kwargs)

    def _wait_mpv_network_demuxer_ready(self, *args, **kwargs):
        return self._playback_network._wait_mpv_network_demuxer_ready(*args, **kwargs)

    def _wait_mpv_stream_ready(self, *args, **kwargs):
        return self._playback_network._wait_mpv_stream_ready(*args, **kwargs)

    def _wait_mpv_ytdl_stream_opening(self, *args, **kwargs):
        return self._playback_network._wait_mpv_ytdl_stream_opening(*args, **kwargs)

    def _ytdl_stream_open_progress(self, *args, **kwargs):
        return self._playback_network._ytdl_stream_open_progress(*args, **kwargs)

    def _is_external_stream_provider(
        self, *, provider: Optional[str] = None, stream_url: Optional[str] = None
    ) -> bool:
        return is_external_stream_provider(provider=provider, stream_url=stream_url)

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
        return self._playback_eof.wait_video_end(
            playlist_id,
            is_network=is_network,
            stream_ready=stream_ready,
            poll_sec=poll_sec,
            stream_url=stream_url,
            provider=provider,
            media_key=media_key,
            item=item,
            is_audio=is_audio,
        )





    def _play_local_video_engine(
        self,
        *,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int,
        profile_muted: bool,
        profile_settings: Dict[str, Any],
        playlist: Any = None,
        playlist_name: Optional[str] = None,
        mode: str,
        source: str = "manual",
        rule_id: Optional[int] = None,
        play_seq: Optional[int] = None,
    ) -> bool:
        """A2 single local video (loop-file=inf) or A1 mpv internal M3U playlist."""
        start_index = int(start_index or 0) % len(items)
        if play_seq is None:
            play_seq = self._begin_play_seq()
        self._active_playlist_id = playlist_id
        self._active_playback_mode = mode
        self._audio_route_applied_for_play = False
        name = playlist_name
        if not name and playlist is not None:
            name = str(getattr(playlist, "name", "") or "")
        # Avoid ORM use after caller released the session.
        playlist = None
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

        # DB work already done by caller; ensure connection is free before loadfile.
        self._release_db_session()

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

        def _abort_markers() -> None:
            self._set_playback_active_marker(False)
            try:
                self._mpv_manager.set_playback_session_active(False)
            except Exception:
                pass

        def _start_thread() -> None:
            self._play_thread = Thread(target=thread_target, args=thread_args, daemon=True)
            self._play_thread.start()

        def _persist() -> None:
            self._persist_playback_status(
                playlist_id=playlist_id,
                status="playing",
                source=source,
                rule_id=int(rule_id) if source == "schedule" and rule_id is not None else None,
                clear_rule=(str(source) != "schedule"),
            )

        if not self._commit_play(
            int(play_seq),
            start_thread=_start_thread,
            persist=_persist,
            on_abort=_abort_markers,
        ):
            self.logger.info(
                "Local video play aborted: superseded by stop/newer play",
                extra={"playlist_id": playlist_id, "play_seq": play_seq, "mode": mode},
            )
            return False

        try:
            if self.socketio:
                self.socketio.emit(
                    "playback_update",
                    {
                        "status": "playing",
                        "playlist_id": int(playlist_id),
                        "source": source,
                        "rule_id": rule_id if source == "schedule" else None,
                        "current_media": self._get_current_media_label(),
                        "playlist": {"id": int(playlist_id), "name": str(name or "")},
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
                    self._on_playlist_item_start(
                        playlist_id, item, profile_muted=profile_muted
                    )
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

    def _halt_mpv_playback(self, *, lock_wait: float = 2.0, timeout: float = 2.0) -> bool:
        """Clear mpv autoplay loops and stop so local_single/m3u cannot keep rolling after Stop.

        Python thread teardown alone is not enough: A2 uses loop-file=inf and A1 uses
        loop-playlist=yes — those keep playing without a live slideshow thread.
        """
        ok = True
        cmds = (
            ["set_property", "loop-file", "no"],
            ["set_property", "loop-playlist", "no"],
            ["stop"],
        )
        for cmd in cmds:
            try:
                resp = self._mpv_manager._send_command(
                    {"command": cmd},
                    timeout=float(timeout),
                    lock_wait=float(lock_wait),
                    max_attempts=1,
                )
                if not resp or resp.get("error") != "success":
                    ok = False
            except Exception:
                ok = False
        return ok

    def _mpv_path_is_network(self, path: Any) -> bool:
        p = str(path or "").strip()
        return p.startswith(("http://", "https://", "ytdl://"))

    def _mpv_loop_props_on(self) -> bool:
        """A1/A2 leave loop-* on after the Python thread dies — Stop must clear them."""
        on_vals = (True, "yes", "inf", "force")
        try:
            lf = self._mpv_get_light("loop-file", timeout=1.0)
        except Exception:
            lf = None
        try:
            lp = self._mpv_get_light("loop-playlist", timeout=1.0)
        except Exception:
            lp = None
        return lf in on_vals or lp in on_vals

    def _mpv_content_still_on_air(self) -> bool:
        """True if mpv still shows real content after a Stop/halt attempt."""
        if self._mpv_has_active_media():
            return True
        if not self._mpv_loop_props_on():
            return False
        try:
            path = self._mpv_get_light("path", timeout=1.0)
        except Exception:
            path = None
        if not path or not str(path).strip():
            return False
        return not self._mpv_path_is_idle_logo(path)

    def _mpv_needs_hard_halt(self) -> bool:
        """True when soft prepare must not skip ``stop`` (ytdl open / A1/A2 loops)."""
        try:
            # Strict True — MagicMock attrs are truthy and would always hard-halt in tests.
            if getattr(self._mpv_manager, "_playback_stream_opening", False) is True:
                return True
        except Exception:
            pass
        if self._mpv_loop_props_on():
            return True
        try:
            path = self._mpv_get_light("path", timeout=1.0)
        except Exception:
            path = None
        if self._mpv_path_is_network(path):
            return True
        if path is None:
            # IPC unknown during ytdl — prefer halt over leaving a stuck open.
            try:
                idle = self._mpv_get_light("idle-active", timeout=0.8)
            except Exception:
                idle = None
            if idle is None:
                return True
        return False

    def _prepare_mpv_for_new_play(self, *, lock_wait: float = 1.5) -> None:
        """Clear A1/A2 loops; full stop when content (incl. opening ytdl) is on air.

        Calling ``stop`` while truly idle (logo / empty) blanks Wayland to imv and
        contends IPC — skip that. But ytdl:// often reports idle-active=true while
        the hook is still opening; skipping stop there left VK/Rutube alive and
        broke the next offline Play.
        """
        if self._mpv_needs_hard_halt():
            self._halt_mpv_playback(lock_wait=float(lock_wait), timeout=3.0)
            return
        try:
            idle = self._mpv_get_light("idle-active", timeout=1.0)
        except Exception:
            idle = None
        already_idle = idle is True or self._mpv_showing_idle_logo()
        if already_idle or not self._mpv_has_active_media():
            for cmd in (
                ["set_property", "loop-file", "no"],
                ["set_property", "loop-playlist", "no"],
            ):
                try:
                    self._mpv_manager._send_command(
                        {"command": cmd},
                        timeout=1.5,
                        lock_wait=float(lock_wait),
                        max_attempts=1,
                    )
                except Exception:
                    pass
            return
        self._halt_mpv_playback(lock_wait=float(lock_wait), timeout=2.0)

    def _stop_play_thread(
        self,
        *,
        preserve_stall_tracking: bool = False,
        preserve_loop_position: bool = False,
        join_timeout: float = 2.0,
        halt_mpv: bool = False,
    ):
        # Invalidate before join so a concurrent play() finishing loadfile aborts.
        self._bump_play_seq()
        # Bump run id *before* clearing stop_event so a ytdl ensure() still inside
        # open-wait cannot re-issue loadfile / persist idle over the next Play.
        self._bump_playback_run_id()

        alive: List[Any] = []
        if self._play_thread is not None and self._play_thread.is_alive():
            alive.append(self._play_thread)
        for thr in list(getattr(self, "_orphan_play_threads", None) or []):
            if thr is not None and thr.is_alive() and thr not in alive:
                alive.append(thr)
        self._orphan_play_threads = []

        if alive:
            self._stop_event.set()
            join_sec = max(0.1, float(join_timeout))
            for thr in alive:
                try:
                    thr.join(timeout=join_sec)
                except Exception:
                    pass
            still_alive = [thr for thr in alive if thr.is_alive()]
            if still_alive:
                self._orphan_play_threads = still_alive
                self.logger.warning(
                    "Playback thread did not exit before join timeout",
                    extra={
                        "event": "playback_thread_join_timeout",
                        "join_timeout_sec": join_sec,
                        "orphan_threads": len(still_alive),
                        "playback_run_id": int(self._playback_run_id),
                    },
                )

        self._play_thread = None
        # Always clear — orphans must abort via playback_run_id, not a sticky stop bit
        # that would make the next Play's slideshow exit immediately.
        self._stop_event.clear()
        self._item_skip_event.clear()
        with self._item_skip_lock:
            self._item_skip_direction = "next"
        self._active_playback_mode = None
        self._clear_stall_restart_pending()
        if not preserve_stall_tracking:
            self._reset_stall_tracking()
        self._active_playlist_id = None
        self._preloaded_stream_ready = False
        self._preloaded_load_cmd = None
        self._preloaded_load_ipc_ok = True
        self._clear_current_media_label(emit=False)
        if not preserve_loop_position:
            self._clear_loop_position()
        self._set_playback_active_marker(False)
        try:
            self._mpv_manager.set_playback_session_active(False)
        except Exception:
            pass
        if halt_mpv:
            # Soft prepare: avoid stop-when-already-idle (IPC contention / Wayland stub).
            try:
                self._prepare_mpv_for_new_play(lock_wait=1.0)
            except Exception:
                pass

    def _manual_slideshow_loop(
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
        return self._playback_slideshow.run(
            playlist_id,
            items,
            start_index,
            first_item_preloaded=first_item_preloaded,
            profile_muted=profile_muted,
            single_pass=single_pass,
            playback_run_id=playback_run_id,
        )

    def _run_manual_slideshow_loop(
        self,
        playlist_id: int,
        items: List[Dict[str, Any]],
        start_index: int = 0,
        *,
        first_item_preloaded: bool = False,
        profile_muted: bool = False,
        single_pass: bool = False,
        playback_run_id: Optional[int] = None,
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
                    single_pass=single_pass,
                    playback_run_id=playback_run_id,
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

        finally:
            self._maybe_return_after_override()

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
        now_mono = time.monotonic()
        entry["failures"] = failures
        entry["next_try_monotonic"] = next_try
        entry["last_touch_monotonic"] = now_mono
        self._media_backoff[media_key] = entry
        self._prune_media_backoff(now_mono=now_mono)
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

    def _prune_media_backoff(self, *, now_mono: Optional[float] = None) -> int:
        from .media_backoff import prune_stale_media_backoff

        removed = prune_stale_media_backoff(self._media_backoff, now=now_mono)
        if removed:
            self.logger.debug(
                "Pruned stale media backoff entries",
                extra={"event": "media_backoff_prune", "removed": removed},
            )
        return removed

    def _play_impl(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
        single_pass: bool = False,
        source: str = "manual",
        rule_id: Optional[int] = None,
    ) -> bool:
        return self._playback_play.run(
            playlist_id,
            start_index=start_index,
            preserve_stall_tracking=preserve_stall_tracking,
            single_pass=single_pass,
            source=source,
            rule_id=rule_id,
        )

    def play(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
        source: str = "manual",
        rule_id: Optional[int] = None,
    ) -> bool:
        """Play playlist with profile support.

        Does **not** hold ``_play_start_lock`` across loadfile/ytdl — that blocked
        Stop→Play for minutes (play_lock_timeout). Handoff lock is only around
        ``_stop_play_thread`` + ``_begin_play_seq`` inside PlaybackPlayRunner.
        """
        with self._app_context():
            with self._override_lock:
                self._override_return_ctx = None
                self._playlist_single_pass = False
            return self._play_impl(
                playlist_id,
                start_index=start_index,
                preserve_stall_tracking=preserve_stall_tracking,
                single_pass=False,
                source=source,
                rule_id=rule_id,
            )

    def _play_lock_timeout_sec(self) -> float:
        """Handoff lock only (not full loadfile). Keep short so Stop→Play stays responsive."""
        try:
            return float((os.getenv("DSIGN_PLAY_LOCK_TIMEOUT_SEC") or "8").strip())
        except ValueError:
            return 8.0

    def _acquire_play_handoff(self, *, playlist_id: int) -> bool:
        """Serialize stop-previous + begin-seq. Never held across loadfile."""
        lock_timeout = max(0.05, min(60.0, float(self._play_lock_timeout_sec())))
        for attempt in range(2):
            acquired = self._play_start_lock.acquire(timeout=lock_timeout)
            if acquired:
                return True
            self.logger.warning(
                "play handoff lock busy — aborting in-flight play and retrying",
                extra={
                    "event": "play_lock_timeout",
                    "playlist_id": int(playlist_id),
                    "timeout_sec": lock_timeout,
                    "attempt": attempt + 1,
                },
            )
            try:
                self._bump_playback_run_id()
            except Exception:
                pass
            try:
                self._bump_play_seq()
            except Exception:
                pass
            try:
                self._stop_event.set()
            except Exception:
                pass
            try:
                self._mpv_manager.set_playback_stream_opening(False)
            except Exception:
                pass
            try:
                self._halt_mpv_playback(lock_wait=1.0, timeout=1.5)
            except Exception:
                pass
            lock_timeout = min(lock_timeout, 5.0)
        self.logger.warning(
            "play handoff lock timeout — giving up",
            extra={
                "event": "play_lock_timeout",
                "playlist_id": int(playlist_id),
                "timeout_sec": lock_timeout,
            },
        )
        return False

    def _release_play_handoff(self) -> None:
        try:
            self._play_start_lock.release()
        except Exception:
            pass

    def claim_playback_intent(
        self,
        playlist_id: int,
        *,
        source: str = "manual",
        rule_id: Optional[int] = None,
    ) -> None:
        """Persist intended source before async loadfile so schedule ticks do not race."""
        src = str(source or "manual")
        with self._app_context():
            self._persist_playback_status(
                playlist_id=int(playlist_id),
                status="playing",
                source=src,
                rule_id=int(rule_id) if src == "schedule" and rule_id is not None else None,
                clear_rule=(src != "schedule"),
            )

    def play_override(
        self,
        playlist_id: int,
        *,
        return_to_previous: bool = True,
        start_index: int = 0,
    ) -> Dict[str, Any]:
        """
        B3: emergency playlist — play once, then optionally resume the previous playlist.
        """
        previous: Optional[Dict[str, Any]] = None
        with self._override_lock:
            if return_to_previous and self._active_playlist_id is not None:
                item_index, _item_count = self._get_loop_position_snapshot()
                previous = {
                    "playlist_id": int(self._active_playlist_id),
                    "item_index": int(item_index or 0),
                }
                self._override_return_ctx = dict(previous)
            else:
                self._override_return_ctx = None
            self._playlist_single_pass = True

        self.logger.info(
            "Playback override requested",
            extra={
                "override_playlist_id": int(playlist_id),
                "return_to_previous": bool(return_to_previous),
                "previous": previous,
                "start_index": int(start_index or 0),
            },
        )

        with self._app_context():
            ok = self._play_impl(
                int(playlist_id),
                start_index=int(start_index or 0),
                preserve_stall_tracking=True,
                single_pass=True,
                source="override",
                rule_id=None,
            )

        return {
            "success": bool(ok),
            "playlist_id": int(playlist_id),
            "return_to_previous": bool(return_to_previous),
            "previous": previous,
        }

    def _maybe_return_after_override(self) -> None:
        """Resume pre-override playlist after a single-pass emergency loop ends."""
        ctx: Optional[Dict[str, Any]] = None
        with self._override_lock:
            if self._playlist_single_pass and self._override_return_ctx:
                ctx = dict(self._override_return_ctx)
            self._override_return_ctx = None
            self._playlist_single_pass = False

        if not ctx:
            return

        if self._on_override_return is not None:
            self.logger.info("Playback override: delegating return to schedule handler", extra=ctx)
            try:
                with self._app_context():
                    self._on_override_return()
            except Exception as e:
                self.logger.error(
                    "Playback override: schedule return handler failed",
                    extra={
                        "error": str(e),
                        "type": type(e).__name__,
                        "previous": ctx,
                    },
                )
            return

        self.logger.info("Playback override: auto-return to previous playlist", extra=ctx)
        try:
            with self._app_context():
                self._play_impl(
                    int(ctx["playlist_id"]),
                    start_index=int(ctx.get("item_index") or 0),
                    preserve_stall_tracking=True,
                    single_pass=False,
                )
        except Exception as e:
            self.logger.error(
                "Playback override: auto-return failed",
                extra={
                    "error": str(e),
                    "type": type(e).__name__,
                    "previous": ctx,
                },
            )


    def stop(
        self,
        *,
        show_idle_logo: bool = True,
        update_status: bool = True,
        preserve_stall_tracking: bool = False,
        preserve_loop_position: bool = False,
        source: str = "manual",
        join_timeout: float = 2.0,
        stop_generation: Optional[int] = None,
    ) -> bool:
        """Stop playback and persist stopped state so UI/API match MPV (idle logo)."""
        with self._app_context():
            return self._stop_impl(
                show_idle_logo=show_idle_logo,
                update_status=update_status,
                preserve_stall_tracking=preserve_stall_tracking,
                preserve_loop_position=preserve_loop_position,
                source=source,
                join_timeout=join_timeout,
                stop_generation=stop_generation,
            )

    def _stop_impl(
        self,
        *,
        show_idle_logo: bool = True,
        update_status: bool = True,
        preserve_stall_tracking: bool = False,
        preserve_loop_position: bool = False,
        source: str = "manual",
        join_timeout: float = 2.0,
        stop_generation: Optional[int] = None,
    ) -> bool:
        from ..models import PlaybackStatus

        try:
            # Fast path: Play already bumped run_id after enqueue_stop — do not tear
            # down the new play (that left Stop OK / Play dead).
            if stop_generation is not None and not self._is_playback_run_current(
                int(stop_generation)
            ):
                self.logger.info(
                    "Stop skipped: superseded by newer play",
                    extra={
                        "event": "stop_superseded",
                        "stop_generation": int(stop_generation),
                        "playback_run_id": int(self._playback_run_id),
                    },
                )
                return True

            # Same lock order as Play handoff: handoff → control (avoid deadlock).
            if not self._acquire_play_handoff(playlist_id=0):
                self.logger.info(
                    "Stop skipped: play handoff busy (newer play starting)",
                    extra={"event": "stop_superseded", "reason": "handoff_busy"},
                )
                return True
            try:
                with self._control_lock:
                    if stop_generation is not None and not self._is_playback_run_current(
                        int(stop_generation)
                    ):
                        self.logger.info(
                            "Stop skipped: superseded under lock",
                            extra={
                                "event": "stop_superseded",
                                "stop_generation": int(stop_generation),
                                "playback_run_id": int(self._playback_run_id),
                            },
                        )
                        return True

                    playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
                    last_playlist_id = playback.playlist_id
                    # Allow a fresh idle-logo retry after this Stop (Play cancels via epoch).
                    self._play_start_mono = 0.0

                    # Soft-prepare inside stop_play_thread clears A1/A2 loops when halt_mpv.
                    self._stop_play_thread(
                        preserve_stall_tracking=preserve_stall_tracking,
                        preserve_loop_position=preserve_loop_position,
                        join_timeout=join_timeout,
                        halt_mpv=True,
                    )
                    stop_run_id = int(self._playback_run_id)
                    self._cancel_content_cache_prefetches()
                    try:
                        self._logo_manager.ensure_mpv_video_output()
                    except Exception:
                        pass
                    self._set_playback_active_marker(False)

                    # Persist stopped/idle BEFORE idle-logo IPC, under the same lock as play
                    # commit so a late schedule play cannot resurrect status=playing.
                    # source=manual holds ScheduleEngine off until return-to-schedule.
                    if update_status:
                        if source == "schedule":
                            self._persist_playback_status(
                                playlist_id=None,
                                status="idle",
                                source="idle",
                                clear_rule=True,
                            )
                        else:
                            self._persist_playback_status(
                                playlist_id=last_playlist_id,
                                status="stopped",
                                source="manual",
                                clear_rule=True,
                            )

                        try:
                            if self.socketio:
                                emit_status = "idle" if source == "schedule" else "stopped"
                                emit_playlist = None if source == "schedule" else last_playlist_id
                                self.socketio.emit(
                                    'playback_update',
                                    {
                                        'status': emit_status,
                                        'playlist_id': emit_playlist,
                                        'current_media': None,
                                    },
                                )
                        except Exception:
                            pass
            finally:
                self._release_play_handoff()

            ok = True
            if show_idle_logo:
                # Async Stop can finish after a newer Play already opened ytdl — never
                # force-restart mpv or show idle logo over that play.
                if not self._is_playback_run_current(stop_run_id):
                    self.logger.info(
                        "Stop mpv/logo skipped: superseded by newer play",
                        extra={
                            "event": "stop_cleanup_superseded",
                            "stop_run_id": int(stop_run_id),
                            "playback_run_id": int(self._playback_run_id),
                        },
                    )
                    return True if update_status else ok
                # Prove mpv is actually idle. Schedule A2 (loop-file=inf) kept playing
                # after a "successful" Stop when we only force-restarted for ytdl.
                try:
                    self._mpv_manager.set_playback_stream_opening(False)
                except Exception:
                    pass
                halted = False
                try:
                    halted = bool(self._halt_mpv_playback(lock_wait=15.0, timeout=3.0))
                except Exception:
                    halted = False
                if not self._is_playback_run_current(stop_run_id):
                    return True if update_status else ok
                still_on = False
                try:
                    still_on = (not halted) or self._mpv_content_still_on_air()
                except Exception:
                    still_on = not halted
                if still_on:
                    self.logger.warning(
                        "Stop left content on air; forcing mpv restart",
                        extra={
                            "event": "stop_force_mpv_restart",
                            "halted": bool(halted),
                            "loop_props_on": bool(self._mpv_loop_props_on()),
                        },
                    )
                    try:
                        halted = bool(
                            self._mpv_manager._force_restart_mpv_for_hung_recovery()
                        )
                    except Exception:
                        halted = False
                    if not self._is_playback_run_current(stop_run_id):
                        return True if update_status else ok
                    try:
                        still_on = self._mpv_content_still_on_air()
                    except Exception:
                        still_on = not halted
                ok = bool(self._logo_manager.display_idle_logo(lock_wait=3.0))
                if not ok or not halted or still_on:
                    self._enqueue_idle_logo_retry()
            return True if update_status else ok
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

    def _enqueue_idle_logo_retry(self) -> None:
        """Best-effort logo after Stop when mpv IPC was busy during the request."""
        app = self._app
        epoch = self._idle_logo_epoch_current()

        def _run() -> None:
            try:
                if epoch != self._idle_logo_epoch_current():
                    return
                # Play/enqueue began — never clobber an in-flight or just-started play.
                if float(self._play_start_mono or 0.0) > 0 and (
                    time.monotonic() - float(self._play_start_mono)
                ) < 120.0:
                    return
                if app is not None:
                    with app.app_context():
                        if epoch != self._idle_logo_epoch_current():
                            return
                        try:
                            self._halt_mpv_playback(lock_wait=30.0, timeout=3.0)
                        except Exception:
                            pass
                        if epoch != self._idle_logo_epoch_current():
                            return
                        self._logo_manager.display_idle_logo(lock_wait=30.0)
                else:
                    if epoch != self._idle_logo_epoch_current():
                        return
                    try:
                        self._halt_mpv_playback(lock_wait=30.0, timeout=3.0)
                    except Exception:
                        pass
                    if epoch != self._idle_logo_epoch_current():
                        return
                    self._logo_manager.display_idle_logo(lock_wait=30.0)
            except Exception as exc:
                self.logger.warning(
                    "Background idle logo after stop failed",
                    extra={"error": str(exc), "type": type(exc).__name__},
                )

        Thread(target=_run, name="idle-logo-retry", daemon=True).start()

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

    def _consume_item_skip(self) -> Optional[str]:
        if not self._item_skip_event.is_set():
            return None
        with self._item_skip_lock:
            if not self._item_skip_event.is_set():
                return None
            self._item_skip_event.clear()
            return self._item_skip_direction

    @staticmethod
    def _mpv_path_is_idle_logo(path: Any) -> bool:
        p = str(path or "").strip()
        if not p:
            return False
        return "idle_logo" in p or p.endswith("placeholder.jpg")

    def _clear_ghost_playing_after_slideshow_exit(
        self,
        playlist_id: int,
        playback_run_id: int,
    ) -> None:
        """Drop ghost DB playing when our slideshow thread exits without supersession."""
        if not self._is_playback_run_current(int(playback_run_id)):
            return
        try:
            if self._mpv_has_active_media():
                return
        except Exception:
            pass
        from ..models import PlaybackStatus

        try:
            with self._control_lock:
                row = self.db_session.query(PlaybackStatus).get(1)
                if row is None:
                    return
                if str(row.status or "").lower() != "playing":
                    return
                if int(row.playlist_id or 0) != int(playlist_id):
                    return
                # Keep manual lock — idle here lets ScheduleEngine steal the screen.
                src = str(getattr(row, "source", None) or "idle").lower()
                if src in ("manual", "override"):
                    status, source, keep_pid = "stopped", "manual", int(playlist_id)
                else:
                    status, source, keep_pid = "idle", "idle", None
                self._play_start_mono = 0.0
                self._persist_playback_status(
                    playlist_id=keep_pid,
                    status=status,
                    source=source,
                    clear_rule=True,
                )
            try:
                if self.socketio:
                    self.socketio.emit(
                        "playback_update",
                        {
                            "status": status,
                            "playlist_id": keep_pid,
                            "source": source,
                            "current_media": None,
                        },
                    )
            except Exception:
                pass
            self.logger.info(
                "Cleared ghost playing after slideshow exit",
                extra={
                    "event": "slideshow_ghost_playing_clear",
                    "playlist_id": int(playlist_id),
                    "playback_run_id": int(playback_run_id),
                    "rollback_status": status,
                    "rollback_source": source,
                },
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to clear ghost playing after slideshow exit",
                extra={
                    "playlist_id": int(playlist_id),
                    "error": str(exc),
                    "type": type(exc).__name__,
                },
            )

    def _any_play_threads_alive(self) -> bool:
        if self._play_thread is not None and self._play_thread.is_alive():
            return True
        for thr in list(getattr(self, "_orphan_play_threads", None) or []):
            if thr is not None and thr.is_alive():
                return True
        return False

    def rollback_claimed_play(
        self,
        playlist_id: int,
        *,
        reason: str,
        claim_source: str = "manual",
    ) -> None:
        """Drop ghost playing when enqueue_play claimed DB but play() never started.

        Manual claims roll back to ``stopped``/``manual`` so ScheduleEngine cannot
        immediately steal the screen (idle→schedule→claim→fail loops).
        """
        if self._any_play_threads_alive():
            return
        from ..models import PlaybackStatus

        src = str(claim_source or "manual").lower()
        if src in ("manual", "override"):
            status, source, keep_pid = "stopped", "manual", int(playlist_id)
            clear_rule = True
        else:
            status, source, keep_pid = "idle", "idle", None
            clear_rule = True

        try:
            with self._control_lock:
                row = self.db_session.query(PlaybackStatus).get(1)
                if row is None:
                    return
                if str(row.status or "").lower() != "playing":
                    return
                if int(row.playlist_id or 0) != int(playlist_id):
                    return
                # Drop start grace so desync can clear immediately (schedule reclaim
                # used to refresh mark_play_starting and block clear forever).
                self._play_start_mono = 0.0
                self._persist_playback_status(
                    playlist_id=keep_pid,
                    status=status,
                    source=source,
                    clear_rule=clear_rule,
                )
            try:
                if self.socketio:
                    self.socketio.emit(
                        "playback_update",
                        {
                            "status": status,
                            "playlist_id": keep_pid,
                            "source": source,
                            "current_media": None,
                        },
                    )
            except Exception:
                pass
            self.logger.warning(
                "Rolled back claimed play — loadfile never started",
                extra={
                    "event": "claimed_play_rollback",
                    "playlist_id": int(playlist_id),
                    "reason": str(reason),
                    "claim_source": src,
                    "rollback_status": status,
                    "rollback_source": source,
                },
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to roll back claimed play",
                extra={
                    "playlist_id": int(playlist_id),
                    "reason": str(reason),
                    "error": str(exc),
                    "type": type(exc).__name__,
                },
            )

    def _mpv_showing_idle_logo(self) -> bool:
        try:
            idle = self._mpv_get_light("idle-active", timeout=1.0)
        except Exception:
            idle = None
        try:
            path = self._mpv_get_light("path", timeout=1.0)
        except Exception:
            path = None
        if idle is True and (not path or not str(path).strip()):
            return True
        return self._mpv_path_is_idle_logo(path)

    def _remote_playback_snapshot(self) -> Dict[str, Any]:
        from ..models import PlaybackStatus

        row = self.db_session.query(PlaybackStatus).first()
        thread_alive = self._any_play_threads_alive()
        return {
            "thread_alive": thread_alive,
            "active_playlist_id": self._active_playlist_id,
            "playback_mode": self._active_playback_mode,
            "db_status": getattr(row, "status", None) if row else None,
            "db_playlist_id": getattr(row, "playlist_id", None) if row else None,
            "mpv_session": bool(self._mpv_manager._playback_session_active),
            "mpv_idle": self._mpv_get_light("idle-active", timeout=2.0),
            "idle_logo": self._mpv_showing_idle_logo(),
        }

    def _mpv_has_active_media(self) -> bool:
        """True only for real content — idle logo must not count as 'playing'.

        Opening ytdl:// often still reports idle-active=true; treat that as active
        so Stop/prepare do not skip the hard halt.
        """
        try:
            if getattr(self._mpv_manager, "_playback_stream_opening", False) is True:
                return True
        except Exception:
            pass
        path = self._mpv_get_light("path", timeout=2.0)
        if self._mpv_path_is_network(path):
            return True
        idle = self._mpv_get_light("idle-active", timeout=2.0)
        if idle is True:
            return False
        if not path or not str(path).strip():
            return False
        if self._mpv_path_is_idle_logo(path):
            return False
        return True

    def _remote_playback_controllable(self) -> tuple[bool, Dict[str, Any]]:
        snap = self._remote_playback_snapshot()
        thread_ok = bool(
            snap["thread_alive"]
            and snap["active_playlist_id"] is not None
        )
        db_ok = snap["db_status"] == "playing" and snap["db_playlist_id"] is not None
        # Session marker alone is not enough after halt left the logo on screen.
        mpv_ok = bool(self._mpv_has_active_media())
        return (thread_ok or (db_ok and mpv_ok)), snap

    def _remote_not_playing(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "not_playing",
            "hint": (
                "Start a playlist first (UI or POST /api/playback/play). "
                "If content is on screen after a service restart, wait for boot "
                "resume or start the playlist again."
            ),
            "playback": snap,
        }

    def _remote_playback_active(self) -> bool:
        ok, _snap = self._remote_playback_controllable()
        return ok

    def _mpv_command_ok(self, response: Optional[Dict[str, Any]]) -> bool:
        return bool(response and response.get("error") == "success")

    def remote_pause(self, paused: Optional[bool] = None) -> Dict[str, Any]:
        """Pause or resume current MPV playback (video/audio; images keep timer)."""
        ok, snap = self._remote_playback_controllable()
        if not ok:
            return self._remote_not_playing(snap)
        try:
            if paused is None:
                current = self._mpv_get_light("pause", timeout=2.0)
                paused = not bool(current)
            target = bool(paused)
            response = self._mpv_manager._send_command(
                {"command": ["set_property", "pause", "yes" if target else "no"]},
                timeout=3.0,
            )
            if not self._mpv_command_ok(response):
                return {
                    "success": False,
                    "error": "mpv_pause_failed",
                    "paused": target,
                }
            return {"success": True, "paused": target}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remote_seek(self, position_sec: float) -> Dict[str, Any]:
        """Seek to absolute position in the current media file."""
        ok, snap = self._remote_playback_controllable()
        if not ok:
            return self._remote_not_playing(snap)
        try:
            position = max(0.0, float(position_sec))
            response = self._mpv_manager._send_command(
                {"command": ["seek", position, "absolute"]},
                timeout=5.0,
            )
            if not self._mpv_command_ok(response):
                return {
                    "success": False,
                    "error": "mpv_seek_failed",
                    "position": position,
                }
            return {"success": True, "position": position}
        except (TypeError, ValueError):
            return {"success": False, "error": "invalid position"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remote_skip(self, direction: str = "next") -> Dict[str, Any]:
        """Skip to next/previous playlist item."""
        snap = self._remote_playback_snapshot()
        normalized = (direction or "next").strip().lower()
        if normalized in ("prev", "back", "backward"):
            normalized = "previous"
        if normalized not in ("next", "previous"):
            return {"success": False, "error": "invalid direction"}

        mode = snap.get("playback_mode") or "manual"
        if mode == "local_playlist":
            if not (snap["thread_alive"] or snap["mpv_session"] or self._mpv_has_active_media()):
                return self._remote_not_playing(snap)
            mpv_cmd = "playlist-next" if normalized == "next" else "playlist-prev"
            response = self._mpv_manager._send_command(
                {"command": [mpv_cmd, "force"]},
                timeout=5.0,
            )
            if not self._mpv_command_ok(response):
                return {
                    "success": False,
                    "error": "mpv_skip_failed",
                    "direction": normalized,
                    "playback_mode": mode,
                }
            item_index, item_count = self._get_loop_position_snapshot()
            return {
                "success": True,
                "direction": normalized,
                "playback_mode": mode,
                "item_index": item_index,
                "item_count": item_count,
            }

        if mode == "local_single":
            if not (snap["thread_alive"] or snap["mpv_session"] or self._mpv_has_active_media()):
                return self._remote_not_playing(snap)
            return {
                "success": True,
                "direction": normalized,
                "playback_mode": mode,
                "note": "single_item_playlist",
                "item_index": 0,
                "item_count": 1,
            }

        thread_ok = bool(
            snap["thread_alive"] and snap["active_playlist_id"] is not None
        )
        if not thread_ok:
            return self._remote_not_playing(snap)

        with self._item_skip_lock:
            self._item_skip_direction = normalized
            self._item_skip_event.set()
        item_index, item_count = self._get_loop_position_snapshot()
        return {
            "success": True,
            "direction": normalized,
            "playback_mode": mode,
            "item_index": item_index,
            "item_count": item_count,
        }

    def _apply_remote_skip_after_item(self, item_index: int, items_count: int) -> Optional[int]:
        """
        After interrupting the current item, return a new loop start_index for
        'previous', or None to continue the inner item loop normally.
        """
        skip_dir = self._consume_item_skip()
        if not skip_dir:
            return None
        if skip_dir == "previous" and items_count > 0:
            return (int(item_index) - 1) % int(items_count)
        return None

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
        with self._override_lock:
            override_active = bool(self._playlist_single_pass)
            override_return = (
                dict(self._override_return_ctx) if self._override_return_ctx else None
            )

        thread_alive = self._any_play_threads_alive()
        try:
            mpv_session = bool(self._mpv_manager._playback_session_active)
        except Exception:
            mpv_session = False
        try:
            loop_pl = self._mpv_get_light("loop-playlist", timeout=1.0)
        except Exception:
            loop_pl = None
        loop_playlist_on = loop_pl in (True, "yes", "inf", "force")
        try:
            loop_fl = self._mpv_get_light("loop-file", timeout=1.0)
        except Exception:
            loop_fl = None
        loop_file_on = loop_fl in (True, "yes", "inf", "force")
        try:
            mpv_path = str(self._mpv_get_light("path", timeout=1.0) or "")
        except Exception:
            mpv_path = ""
        logo_path = self._mpv_path_is_idle_logo(mpv_path)
        # Orphan: mpv still looping playlist/media while DB/thread say idle.
        # A2 schedule uses loop-file=inf — must count even when session marker is cleared.
        db_playing = str(getattr(status, "status", None) or "").lower() == "playing"
        try:
            media_on = bool(self._mpv_has_active_media()) and not logo_path
        except Exception:
            media_on = False
        orphan_mpv = (not db_playing) and (not thread_alive) and (
            (mpv_session and not logo_path)
            or (loop_playlist_on and bool(mpv_path) and not logo_path)
            or (loop_file_on and bool(mpv_path) and not logo_path)
            or media_on
        )
        # Ghost: DB says playing/schedule but screen only shows idle logo (or nothing).
        content_active = bool(mpv_path) and not logo_path and (
            bool(thread_alive)
            or bool(mpv_session)
            or bool(loop_playlist_on)
            or bool(loop_file_on)
            or media_on
        )
        stale_playing = bool(db_playing) and (not thread_alive) and (not content_active) and (
            logo_path or not mpv_path
        )
        # Grace window while Play loadfile / ytdl open runs (logo may stay on path briefly).
        try:
            start_age = time.monotonic() - float(self._play_start_mono or 0.0)
            starting = start_age < 45.0
            try:
                if getattr(self._mpv_manager, "_playback_stream_opening", False) is True:
                    starting = starting or start_age < 180.0
            except Exception:
                pass
        except Exception:
            starting = False
        if starting and db_playing:
            stale_playing = False

        out_status = status.status if status else None
        out_source = (status.source or 'idle') if status else 'idle'
        out_playlist_id = status.playlist_id if status else None
        out_rule_id = status.rule_id if status else None
        if stale_playing:
            # Heal UI immediately; desync watch will clear DB shortly.
            out_status = "idle"
            out_source = "idle"
            out_playlist_id = None
            out_rule_id = None

        return {
            'status': out_status,
            'playlist_id': out_playlist_id,
            'source': out_source,
            'rule_id': out_rule_id,
            'previous_source': status.previous_source if status else None,
            'previous_rule_id': status.previous_rule_id if status else None,
            'previous_playlist_id': status.previous_playlist_id if status else None,
            'item_index': item_index,
            'item_count': item_count,
            'media_key': media_key,
            'time_pos': mpv_snap.get("time_pos"),
            'duration': mpv_snap.get("duration"),
            'is_network': bool(mpv_snap.get("is_network")),
            'mpv_responsive': bool(mpv_snap.get("mpv_responsive")),
            'thread_alive': thread_alive,
            'mpv_session_active': mpv_session,
            'orphan_mpv': bool(orphan_mpv),
            'stale_playing': bool(stale_playing),
            'idle_logo': bool(logo_path),
            'current_media': None if stale_playing else self._get_current_media_label(),
            'settings': self._mpv_manager._current_settings,
            'network_health': self.get_network_playback_health(),
            'cache_state': cache_state,
            'override': {
                'active': override_active,
                'return_to': override_return,
            },
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
