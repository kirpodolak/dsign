import os
import subprocess
import traceback
import time
from threading import Event, Thread
from typing import Dict, Optional, Any
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

    def set_external_media_service(self, service) -> None:
        """Attach external media resolver service (optional)."""
        self._external_media_service = service

    def _resolve_playlist_item_path(self, file_name: str) -> Optional[Dict[str, Any]]:
        """
        Convert a playlist file_name into a playback dict: {path,is_video,duration,muted}.
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
            url = svc.ensure_fresh_resolved_url(row)
            return {
                "key": str(file_name),
                "path": url,
                "is_video": True,
                "http_headers": (getattr(row, "http_headers", None) or {}),
            }

        # Local file
        file_path = self.upload_folder / str(file_name)
        if not file_path.exists():
            return None
        ext = file_path.suffix.lower()
        is_video = ext in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
        return {"path": str(file_path), "is_video": is_video}

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
            try:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", "path"]},
                    timeout=2.0,
                )
                if resp and resp.get("error") == "success":
                    last_seen = resp.get("data")
                    if last_seen == expected_path:
                        return True
            except Exception:
                pass
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
            try:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", "vo-configured"]},
                    timeout=2.0,
                )
                if resp and resp.get("error") == "success":
                    last_val = resp.get("data")
                    if last_val is True:
                        return True
            except Exception:
                pass
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

    def _mpv_get_prop_bool(self, name: str, timeout: float = 3.0) -> Optional[bool]:
        """Return bool property value, or None if unavailable / error."""
        try:
            resp = self._mpv_manager._send_command(
                {"command": ["get_property", name]},
                timeout=timeout,
            )
        except Exception:
            return None
        if not resp or resp.get("error") != "success":
            return None
        data = resp.get("data")
        if isinstance(data, bool):
            return data
        return None

    def _wait_mpv_leave_idle(self, timeout_sec: float = 45.0) -> bool:
        """
        After loadfile, mpv often reports idle-active=true until the demuxer opens.
        Our old loop treated idle as EOF and skipped network streams in ~1 tick.
        """
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            idle = self._mpv_get_prop_bool("idle-active", timeout=3.0)
            if idle is False:
                return True
            self._stop_event.wait(timeout=0.15)
        return False

    def _stop_play_thread(self):
        if self._play_thread and self._play_thread.is_alive():
            self._stop_event.set()
            try:
                self._play_thread.join(timeout=2.0)
            except Exception:
                pass
        self._play_thread = None
        self._stop_event.clear()
        self._active_playlist_id = None

    def _manual_slideshow_loop(self, playlist_id: int, items: list[dict], start_index: int = 0):
        """
        Manual playback loop that enforces per-item durations for images and plays videos to EOF.
        Runs in a background thread; advances images by sleeping for their duration and videos by
        polling mpv properties until EOF.
        """
        self.logger.info("Starting manual playback loop", extra={"playlist_id": playlist_id, "items_count": len(items)})

        if not items:
            return

        start_index = int(start_index or 0)
        if start_index < 0 or start_index >= len(items):
            start_index = 0

        default_duration = 10
        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            # Iterate cyclically starting from start_index.
            for offset in range(len(items)):
                item = items[(start_index + offset) % len(items)]
                if self._stop_event.is_set() or self._active_playlist_id != playlist_id:
                    break

                path = item["path"]
                is_video = item["is_video"]
                media_key = str(item.get("key") or path)
                http_headers = item.get("http_headers") or {}
                raw_duration = item.get("duration")
                # Only images use duration. Treat 0/None as "missing" for images.
                duration = raw_duration if (raw_duration is not None and int(raw_duration) >= 1) else default_duration
                muted = bool(item.get("muted", False))

                # Apply per-item settings (best-effort, longer timeout to avoid IPC churn on Pi 3B+).
                # NOTE: if MPV is under load, short timeouts cause broken pipes and retry storms.
                try:
                    self._mpv_manager._send_command(
                        # Do NOT loop files. We control the loop at application level,
                        # and looping still images can cause visible "blink" on some builds.
                        {"command": ["set_property", "loop-file", "no"]},
                        timeout=5.0,
                    )
                except Exception:
                    pass

                try:
                    self._mpv_manager._send_command(
                        {"command": ["set_property", "mute", "yes" if muted else "no"]},
                        timeout=5.0,
                    )
                except Exception:
                    pass

                # Load next media file
                load_started = time.monotonic()
                # If this is an external stream requiring headers, apply them before loadfile.
                # MPV expects a single string of "Key: Value\r\nKey2: Value2" for http-header-fields.
                if http_headers and isinstance(http_headers, dict):
                    try:
                        header_lines = []
                        for k, v in http_headers.items():
                            if k is None or v is None:
                                continue
                            ks = str(k).strip()
                            vs = str(v).strip()
                            if not ks or not vs:
                                continue
                            header_lines.append(f"{ks}: {vs}")
                        if header_lines:
                            self._mpv_manager._send_command(
                                {"command": ["set_property", "http-header-fields", "\r\n".join(header_lines)]},
                                timeout=5.0,
                            )
                    except Exception:
                        pass
                load_resp = self._mpv_manager._send_command({"command": ["loadfile", path, "replace"]}, timeout=20.0)
                self._mpv_manager._send_command({"command": ["set_property", "pause", "no"]}, timeout=10.0)
                if not load_resp or load_resp.get("error") != "success":
                    self.logger.warning(
                        "MPV loadfile failed",
                        extra={"path": path, "mpv_response": load_resp},
                    )
                    # Skip to next item; avoid getting stuck on a bad file.
                    continue

                if is_video:
                    # Network streams: mpv stays idle-active until open; do not treat idle as EOF.
                    is_network = isinstance(path, str) and (
                        path.startswith("http://")
                        or path.startswith("https://")
                        or path.startswith("ytdl://")
                    )
                    if is_network:
                        if not self._wait_mpv_leave_idle(timeout_sec=60.0):
                            self.logger.warning(
                                "MPV stayed idle after loadfile (stream failed or blocked)",
                                extra={"media_key": media_key, "path_preview": str(path)[:120]},
                            )
                            continue
                    # Wait until eof-reached is a real bool (some builds return unavailable briefly).
                    prop_deadline = time.monotonic() + 12.0
                    while time.monotonic() < prop_deadline and not self._stop_event.is_set():
                        eof = self._mpv_get_prop_bool("eof-reached", timeout=3.0)
                        if eof is not None:
                            break
                        self._stop_event.wait(timeout=0.2)
                    # Wait until playback ends (eof-reached only; idle-active is not EOF for streams).
                    start = time.time()
                    while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
                        eof = self._mpv_get_prop_bool("eof-reached", timeout=5.0)
                        if eof is True:
                            break
                        # prevent stuck forever: 6 hours max video
                        if time.time() - start > 6 * 3600:
                            break
                        time.sleep(1.0)
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
                    # wait until MPV loaded the file *and* VO is configured, then start countdown.
                    loaded = self._wait_for_mpv_loaded_path(path, timeout=15.0)
                    vo_ready = self._wait_for_mpv_vo_configured(timeout=5.0)
                    timer_start = time.monotonic()
                    load_wait_sec = round(timer_start - load_started, 3)

                    dur_sec = max(1, int(duration))
                    switch_at = timer_start + dur_sec
                    self.logger.debug(
                        "Image timer scheduled",
                        extra={
                            "path": path,
                            "duration_sec": dur_sec,
                            "loaded_confirmed": loaded,
                            "vo_configured": vo_ready,
                            "load_wait_sec": load_wait_sec,
                        },
                    )
                    self._sleep_until(switch_at, step=0.2)

    def play(self, playlist_id: int) -> bool:
        """Play playlist with profile support"""
        from ..models import PlaybackStatus, Playlist, PlaylistProfileAssignment, PlaybackProfile
    
        try:
            # Stop any previous manual playback loop
            self._stop_play_thread()

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
                items.append(
                    {
                        "path": resolved["path"],
                        "duration": int(getattr(pf, "duration", 0) or 0),
                        "is_video": is_video,
                        "muted": bool(getattr(pf, "muted", False)) if is_video else False,
                    }
                )

            if not items:
                raise ValueError(
                    f"Playlist {playlist_id} has no existing media files"
                    + (f". Missing: {', '.join(missing[:10])}" if missing else "")
                    + (" ..." if len(missing) > 10 else "")
                )

            self._active_playlist_id = playlist_id

            # Show first item immediately for responsiveness
            first = items[0]
            try:
                # Do NOT loop the file at MPV level; the app controls looping.
                self._mpv_manager._send_command(
                    {"command": ["set_property", "loop-file", "no"]},
                    timeout=2.0,
                )
            except Exception:
                pass
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "mute", "yes" if bool(first.get("muted")) else "no"]},
                    timeout=2.0,
                )
            except Exception:
                pass
            self._mpv_manager._send_command({"command": ["loadfile", first["path"], "replace"]}, timeout=10.0)
            self._mpv_manager._send_command({"command": ["set_property", "pause", "no"]}, timeout=5.0)

            # Update playback status (single-row table; keep id=1 stable)
            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            playback.playlist_id = playlist_id
            playback.status = 'playing'
            self.db_session.add(playback)
            self.db_session.commit()

            # Start background loop to enforce durations and EOF waits.
            # IMPORTANT: start from the *next* item, because we already loaded the first one above.
            self._play_thread = Thread(
                target=self._manual_slideshow_loop,
                args=(playlist_id, items, 1),
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

            # Fall back to idle logo
            self._logo_manager.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(self) -> bool:
        """Stop playback and persist stopped state so UI/API match MPV (idle logo)."""
        from ..models import PlaybackStatus

        try:
            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            last_playlist_id = playback.playlist_id

            self._stop_play_thread()
            ok = self._logo_manager.display_idle_logo()

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

    def get_status(self) -> Dict:
        """Get current playback status"""
        from ..models import PlaybackStatus
        
        status = self.db_session.query(PlaybackStatus).get(1) or self.db_session.query(PlaybackStatus).first()
        return {
            'status': status.status if status else None,
            'playlist_id': status.playlist_id if status else None,
            'settings': self._mpv_manager._current_settings
        }

    def restart_mpv(self) -> bool:
        """Restart MPV process with enhanced reliability"""
        try:
            if self._mpv_manager._mpv_process:
                try:
                    self._mpv_manager._mpv_process.terminate()
                    self._mpv_manager._mpv_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.logger.warning("MPV process did not terminate gracefully, killing...")
                    self._mpv_manager._mpv_process.kill()
                    self._mpv_manager._mpv_process.wait()
                except Exception as e:
                    self.logger.warning(f"Error terminating MPV process: {str(e)}")
            
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    os.unlink(PlaybackConstants.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Error removing socket: {str(e)}")
                    try:
                        os.chmod(PlaybackConstants.SOCKET_PATH, 0o777)
                        os.unlink(PlaybackConstants.SOCKET_PATH)
                    except:
                        pass
            
            self._mpv_manager._mpv_ready = False
            self._mpv_manager._socket_ready_event.clear()
            self._mpv_manager._ensure_mpv_service()
            
            try:
                self._mpv_manager._wait_for_mpv_ready(timeout=30)
                return True
            except Exception as e:
                self.logger.error(f"Failed to verify MPV restart: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(
                "MPV restart failed",
                extra={
                    "error": str(e),
                    "type": type(e).__name__,
                    "stack_trace": traceback.format_exc()
                }
            )
            return False

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
