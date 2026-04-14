import os
import subprocess
import traceback
import time
from threading import Event, Thread
from typing import Dict, Optional
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

    def _manual_slideshow_loop(self, playlist_id: int, items: list[dict]):
        """
        Manual playback loop that enforces per-item durations for images and plays videos to EOF.
        Runs in a background thread; advances images by sleeping for their duration and videos by
        polling mpv properties until EOF.
        """
        self.logger.info("Starting manual playback loop", extra={"playlist_id": playlist_id, "items_count": len(items)})

        default_duration = 10
        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            for item in items:
                if self._stop_event.is_set() or self._active_playlist_id != playlist_id:
                    break

                path = item["path"]
                is_video = item["is_video"]
                duration = item.get("duration") or default_duration
                muted = bool(item.get("muted", False))

                # Apply per-item settings (best-effort, longer timeout to avoid IPC churn on Pi 3B+).
                # NOTE: if MPV is under load, short timeouts cause broken pipes and retry storms.
                try:
                    self._mpv_manager._send_command(
                        {"command": ["set_property", "loop-file", "no" if is_video else "inf"]},
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
                self._mpv_manager._send_command({"command": ["loadfile", path, "replace"]}, timeout=20.0)
                self._mpv_manager._send_command({"command": ["set_property", "pause", "no"]}, timeout=10.0)

                if is_video:
                    # Wait until playback ends
                    start = time.time()
                    while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
                        # `eof-reached` is the most reliable signal for local files.
                        resp = self._mpv_manager._send_command(
                            {"command": ["get_property", "eof-reached"]},
                            timeout=5.0,
                        )
                        if resp and resp.get("error") == "success" and resp.get("data") is True:
                            break

                        # Fallback for unusual states: if mpv goes idle, treat as ended
                        resp = self._mpv_manager._send_command(
                            {"command": ["get_property", "idle-active"]},
                            timeout=5.0
                        )
                        if resp and resp.get("error") == "success" and resp.get("data") is True:
                            break
                        # prevent stuck forever: 6 hours max video
                        if time.time() - start > 6 * 3600:
                            break
                        time.sleep(1.0)
                else:
                    # Show image for its duration
                    time.sleep(max(1, int(duration)))

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
            for pf in (playlist.files or []):
                file_path = self.upload_folder / pf.file_name
                if not file_path.exists():
                    missing.append(str(file_path))
                    continue

                ext = file_path.suffix.lower()
                is_video = ext in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
                items.append(
                    {
                        "path": str(file_path),
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
                self._mpv_manager._send_command(
                    {"command": ["set_property", "loop-file", "no" if first["is_video"] else "inf"]},
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

            # Start background loop to enforce durations and EOF waits
            self._play_thread = Thread(
                target=self._manual_slideshow_loop,
                args=(playlist_id, items),
                daemon=True,
            )
            self._play_thread.start()

            # Notify clients
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist': {'id': playlist.id, 'name': playlist.name},
                'settings': profile_settings
            })
        
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
