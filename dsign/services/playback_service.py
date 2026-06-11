import os
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Dict, Optional, List, Union, Any
from concurrent.futures import ThreadPoolExecutor
from threading import Thread, Lock, Event
import subprocess
import shutil
import logging

from .mpv_management import MPVManager
from .logo_management import LogoManager
from .profile_management import ProfileManager
from .playlist_management import PlaylistManager
from .playback_constants import PlaybackConstants
from .logger import ServiceLogger

class PlaybackService:
    def __init__(self, upload_folder: str, db_session, socketio, logger: Optional[Union[logging.Logger, ServiceLogger]] = None):
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self.socketio = socketio
        
        # Initialize components
        self._mpv_manager = MPVManager(
            logger=self.logger,
            socketio=self.socketio,
            upload_folder=str(self.upload_folder))
            
        self._logo_manager = LogoManager(
            logger=self.logger,
            socketio=self.socketio,
            upload_folder=str(self.upload_folder),
            db_session=self.db_session,
            mpv_manager=self._mpv_manager
        )
            
        self._profile_manager = ProfileManager(
            self.logger, 
            self.db_session, 
            self._mpv_manager
        )
        
        self._playlist_manager = PlaylistManager(
            self.logger, 
            self.socketio, 
            self.upload_folder,
            self.db_session, 
            self._mpv_manager, 
            self._logo_manager
        )
        
        self.logo_manager = LogoManager(
            logger=self.logger,
            socketio=self.socketio,
            upload_folder=self.upload_folder,
            db_session=self.db_session,
            mpv_manager=self._mpv_manager
        )
        
        self._recover_lock = Lock()
        self._last_socket_identity: Optional[tuple] = None
        self._app = None
        self._app_ready = Event()
        self._mpv_init_ready = Event()

        self._mpv_manager.set_post_restart_callback(self._on_mpv_app_initiated_restart)
        self._playlist_manager.set_slideshow_crash_callback(self._on_slideshow_thread_crash)

        self._log_info(
            "PlaybackService constructed (non-blocking MPV init)",
            extra={"action": "init", "mpv_init_mode": "background"},
        )

        # Do not block Flask bind on MPV IPC (ytdl can stall get_property for 15s+).
        Thread(
            target=self._init_background_loop,
            name="playback-init",
            daemon=True,
        ).start()
        self._start_mpv_socket_watch()
        self._start_mpv_ipc_watchdog()

    def set_app(self, app) -> None:
        """Attach Flask app for DB access from background threads (socket watch, boot resume)."""
        self._app = app
        self._app_ready.set()
        pm = getattr(self, "_playlist_manager", None)
        if pm is not None and hasattr(pm, "set_app"):
            pm.set_app(app)

    def _ensure_app_wired(self, timeout: float = 90.0) -> bool:
        """Background MPV init can finish before init_routes calls set_app."""
        if self._app is not None:
            return True
        return self._app_ready.wait(timeout=max(0.0, float(timeout)))

    def _app_context(self):
        if self._app is None:
            self._ensure_app_wired(timeout=90.0)
        return self._app.app_context() if self._app is not None else nullcontext()

    def display_idle_logo(self):
        return self.logo_manager.display_idle_logo()
        
    def restart_idle_logo(self, upload_folder: Optional[str] = None, idle_logo: Optional[str] = None, rotate: Optional[int] = None):
        """Proxy to logo manager (supports optional rotate)."""
        return self.logo_manager.restart_idle_logo(upload_folder=upload_folder, idle_logo=idle_logo, rotate=rotate)
        
    def _sanitize_extra_data(self, extra_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Remove reserved keys from extra_data to prevent LogRecord conflicts"""
        if not extra_data:
            return None
            
        reserved_keys = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
            'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
            'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
            'thread', 'threadName', 'processName', 'process'
        }
        
        return {k: v for k, v in extra_data.items() if k not in reserved_keys}

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.error(message, exc_info=exc_info, extra=safe_extra)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.info(message, extra=safe_extra)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.warning(message, extra=safe_extra)

    def _init_background_loop(self) -> None:
        """MPV init in background so digital-signage.service / Flask start immediately."""
        delay = 2.0
        while True:
            try:
                self._init_with_retry(max_attempts=1)
                self._mpv_init_ready.set()
                return
            except Exception as e:
                self._log_warning(
                    "Background MPV init failed; retrying",
                    extra={
                        "action": "init",
                        "error": str(e),
                        "type": type(e).__name__,
                        "retry_delay_sec": round(delay, 1),
                    },
                )
                time.sleep(delay)
                delay = min(30.0, delay * 1.5)

    def _init_with_retry(self, max_attempts: int = 3, initial_delay: float = 2.0):
        """Optimized initialization with parallel checks and backoff"""
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                delay = min(initial_delay * (2 ** attempt), 30)
                
                if not self._mpv_manager.wait_for_ipc_socket_at_startup():
                    raise RuntimeError(
                        "MPV IPC socket not available after startup wait "
                        f"(socket={self._mpv_manager.mpv_socket!r})"
                    )

                # Socket readiness — do not require `responsive` here; first IPC ping can race after mpv restart.
                health = self._mpv_manager.check_health()
                if not health.get("socket_available"):
                    raise RuntimeError(f"MPV health check failed (no IPC socket): {health}")

                if not self._mpv_manager.initialize():
                    raise RuntimeError("MPV initialization failed")
                
                if self._should_resume_playback_after_boot():
                    Thread(target=self._resume_playback_after_boot, daemon=True).start()
                else:
                    Thread(target=self._preload_resources).start()
                return
                    
            except Exception as e:
                last_exception = e
                self._log_warning(
                    f"Initialization attempt {attempt + 1} failed",
                    extra={
                        "attempt": attempt + 1,
                        "action": "init",
                        "error": str(e),
                        "type": type(e).__name__,
                    },
                )
                if attempt < max_attempts - 1:
                    time.sleep(delay)

        self._log_warning(
            "Initialization failed after all attempts",
            extra={
                "action": "init",
                "status": "failed",
                "last_error": str(last_exception),
            },
        )
        raise RuntimeError(f"Initialization failed: {str(last_exception)}")

    def _preload_resources(self):
        """Non-critical resource loading in background"""
        try:
            self._logo_manager._initialize_default_logo()
            logo_path = self._logo_manager.get_current_logo_path()
            if not logo_path.exists():
                self._log_warning(
                    "Logo file missing", 
                    extra={
                        'action': 'preload_resources',
                        'logo_path': str(logo_path)
                    }
                )
            
            self._transition_to_idle()
        except Exception as e:
            self._log_error(
                "Background init error", 
                extra={
                    'action': 'preload_resources',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )

    def _mpv_socket_identity(self) -> Optional[tuple]:
        sock = getattr(self._mpv_manager, "mpv_socket", None)
        if not sock:
            return None
        try:
            st = os.stat(sock)
            mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            return (int(st.st_ino), int(mtime))
        except OSError:
            return None

    def _start_mpv_socket_watch(self) -> None:
        if os.getenv("DSIGN_MPV_SOCKET_WATCH", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        self._last_socket_identity = self._mpv_socket_identity()
        Thread(target=self._mpv_socket_watch_loop, name="mpv-socket-watch", daemon=True).start()

    def _start_mpv_ipc_watchdog(self) -> None:
        if os.getenv("DSIGN_MPV_IPC_WATCHDOG", "1").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        ):
            return
        Thread(target=self._mpv_ipc_watchdog_loop, name="mpv-ipc-watchdog", daemon=True).start()

    def _mpv_ipc_watchdog_loop(self) -> None:
        interval = self._mpv_manager._watchdog_interval_sec()
        while True:
            time.sleep(interval)
            try:
                self._mpv_manager.run_playback_ipc_watchdog_probe()
            except Exception as e:
                self._log_warning(
                    "MPV IPC watchdog probe raised",
                    extra={
                        "error": str(e),
                        "type": type(e).__name__,
                        "action": "mpv_ipc_watchdog",
                    },
                )

    def _mpv_socket_watch_loop(self) -> None:
        interval = 3.0
        try:
            interval = float((os.getenv("DSIGN_MPV_SOCKET_WATCH_SEC") or "3").strip())
        except ValueError:
            pass
        interval = max(1.0, min(30.0, interval))
        while True:
            time.sleep(interval)
            ident = self._mpv_socket_identity()
            if ident is None:
                continue
            if self._last_socket_identity is None:
                self._last_socket_identity = ident
                continue
            if ident == self._last_socket_identity:
                continue
            self._last_socket_identity = ident
            if self._mpv_manager.was_recent_app_initiated_restart(within_sec=60.0):
                continue
            if self._recover_lock.locked():
                continue
            self._log_warning(
                "MPV IPC socket recreated (systemd restart?); recovering playback",
                extra={"action": "mpv_socket_watch"},
            )
            try:
                advance = self._playlist_manager.consume_stall_recovery_advance()
                self.recover_after_mpv_systemd_restart(resume_advance=advance)
            except Exception as e:
                self._log_warning(
                    "MPV socket watch recovery failed",
                    extra={"error": str(e), "type": type(e).__name__, "action": "mpv_socket_watch"},
                )

    def _on_slideshow_thread_crash(self) -> None:
        Thread(
            target=self._resume_slideshow_after_crash,
            name="slideshow-crash-recover",
            daemon=True,
        ).start()

    def _resume_slideshow_after_crash(self) -> None:
        try:
            with self._app_context():
                if not self._recover_lock.acquire(blocking=False):
                    return
                try:
                    playlist_id = self._resolve_playlist_id_for_recovery()
                    if playlist_id is None:
                        return
                    resume_index = self._playlist_manager.get_resume_start_index(advance=False)
                    ok = bool(
                        self._playlist_manager.play(
                            playlist_id,
                            start_index=resume_index,
                        )
                    )
                    self._log_warning(
                        "Resumed playlist after slideshow thread crash",
                        extra={
                            "playlist_id": playlist_id,
                            "start_index": resume_index,
                            "ok": ok,
                            "action": "slideshow_recover",
                        },
                    )
                finally:
                    self._recover_lock.release()
        except Exception as e:
            self._log_warning(
                "Slideshow crash recovery failed",
                extra={"error": str(e), "type": type(e).__name__, "action": "slideshow_recover"},
            )

    def _on_mpv_app_initiated_restart(self) -> None:
        """Resume playlist after hung-recovery restart (avoid racing socket-watch recover)."""
        Thread(
            target=self._recover_after_app_mpv_restart,
            name="mpv-post-restart-recover",
            daemon=True,
        ).start()

    def _recover_after_app_mpv_restart(self) -> None:
        try:
            with self._app_context():
                self._last_socket_identity = self._mpv_socket_identity()
                advance = self._playlist_manager.consume_stall_recovery_advance()
                self.recover_after_mpv_systemd_restart(resume_advance=advance)
        except Exception as e:
            self._log_warning(
                "Post-restart playback recovery failed",
                extra={"error": str(e), "type": type(e).__name__, "action": "mpv_recover"},
            )

    def recover_after_mpv_systemd_restart(
        self,
        *,
        restart_playlist: Optional[bool] = None,
        resume_advance: bool = False,
    ) -> bool:
        """Re-bind IPC after `systemctl restart dsign-mpv` without restarting digital-signage."""
        with self._app_context():
            return self._recover_after_mpv_systemd_restart_impl(
                restart_playlist=restart_playlist,
                resume_advance=resume_advance,
            )

    def _recover_after_mpv_systemd_restart_impl(
        self,
        *,
        restart_playlist: Optional[bool] = None,
        resume_advance: bool = False,
    ) -> bool:
        if not self._recover_lock.acquire(blocking=False):
            self._log_warning(
                "MPV recover skipped (already in progress)",
                extra={"action": "mpv_recover"},
            )
            return False
        try:
            return self._recover_after_mpv_systemd_restart_body(
                restart_playlist=restart_playlist,
                resume_advance=resume_advance,
            )
        finally:
            self._recover_lock.release()

    def _recover_after_mpv_systemd_restart_body(
        self,
        *,
        restart_playlist: Optional[bool] = None,
        resume_advance: bool = False,
    ) -> bool:
        playlist_id: Optional[int] = None
        resume_index = 0
        if restart_playlist is not False:
            playlist_id = self._resolve_playlist_id_for_recovery()
            if playlist_id is not None:
                try:
                    resume_index = self._playlist_manager.get_resume_start_index(
                        advance=resume_advance
                    )
                except Exception:
                    resume_index = 0
        try:
            self._playlist_manager.stop(show_idle_logo=False, update_status=False)
        except Exception:
            pass
        self._mpv_manager._reset_ipc_session()
        if not self._mpv_manager.wait_for_ipc_socket_at_startup():
            self._log_warning(
                "MPV recover: socket not available",
                extra={"action": "mpv_recover"},
            )
            return False
        if not self._mpv_manager.initialize():
            self._log_warning(
                "MPV recover: initialize failed",
                extra={"action": "mpv_recover"},
            )
            return False
        self._wait_after_mpv_recover()
        self._last_socket_identity = self._mpv_socket_identity()
        if playlist_id is not None:
            ok = False
            for attempt in range(2):
                try:
                    ok = bool(self.play(playlist_id, start_index=resume_index))
                except Exception as e:
                    ok = False
                    self._log_warning(
                        "MPV recover: play() raised",
                        extra={
                            "playlist_id": playlist_id,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "type": type(e).__name__,
                            "action": "mpv_recover",
                        },
                    )
                if ok:
                    self._log_info(
                        "Resumed playlist after MPV service restart",
                        extra={
                            "playlist_id": playlist_id,
                            "start_index": resume_index,
                            "resume_advance": resume_advance,
                            "action": "mpv_recover",
                        },
                    )
                    return True
                if attempt == 0:
                    time.sleep(2.0)
            self._log_warning(
                "MPV recover: play() failed after mpv restart",
                extra={"playlist_id": playlist_id, "action": "mpv_recover"},
            )
            return False
        if not self._playback_active_marker_exists() and not self._playlist_manager_has_active_playback():
            Thread(target=self._preload_resources, daemon=True).start()
        else:
            self._log_warning(
                "MPV recover: playback looked active but no playlist_id to resume",
                extra={"action": "mpv_recover"},
            )
        return True


    def _network_assistant_interactive_enabled(self) -> bool:
        env_path = Path("/var/lib/dsign/config/network-assistant.env")
        try:
            if not env_path.is_file():
                return False
            for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if line.startswith("DSIGN_NETWORK_ASSISTANT_INTERACTIVE="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val in ("1", "true", "yes", "on")
        except Exception:
            pass
        return False

    def _wait_for_wifi_on_display(self) -> None:
        """After IP OSD, wait for optional Wi-Fi picker on the content display."""
        if not self._network_assistant_interactive_enabled():
            return
        try:
            max_wait = float((os.getenv("DSIGN_BOOT_WIFI_PROMPT_WAIT_SEC") or "90").strip())
        except ValueError:
            max_wait = 90.0
        max_wait = max(15.0, min(300.0, max_wait))
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if Path("/run/dsign/wifi-on-display-done").is_file():
                return
            time.sleep(0.5)

    def _wait_before_boot_playlist(self) -> None:
        """Let startup IP OSD finish before loadfile covers the screen."""
        try:
            max_wait = float((os.getenv("DSIGN_BOOT_PLAYLIST_DELAY_SEC") or "12").strip())
        except ValueError:
            max_wait = 12.0
        max_wait = max(3.0, min(90.0, max_wait))
        try:
            subprocess.run(
                ["sudo", "-n", "/usr/bin/systemctl", "restart", "dsign-show-startup-ip.service"],
                timeout=8.0,
                check=False,
                capture_output=True,
            )
        except Exception:
            pass
        marker = Path("/run/dsign/startup-ip-shown")
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if marker.is_file():
                try:
                    grace = float((os.getenv("DSIGN_BOOT_IP_POST_MARKER_SEC") or "3").strip())
                except ValueError:
                    grace = 3.0
                time.sleep(max(0.0, min(15.0, grace)))
                return
            time.sleep(0.25)
        time.sleep(2.0)
        self._wait_for_wifi_on_display()

    def _playback_active_marker_exists(self) -> bool:
        try:
            return Path("/run/dsign/playback-active").is_file()
        except Exception:
            return False

    def _playlist_manager_has_active_playback(self) -> bool:
        pm = getattr(self, "_playlist_manager", None)
        if pm is None:
            return False
        try:
            if getattr(pm, "_active_playlist_id", None):
                return True
            th = getattr(pm, "_play_thread", None)
            return bool(th is not None and th.is_alive())
        except Exception:
            return False

    def _resolve_playlist_id_for_recovery(self) -> Optional[int]:
        """Pick playlist to resume after mpv-only systemd restart."""
        with self._app_context():
            try:
                from ..models import PlaybackStatus
                session = getattr(self.db_session, "session", self.db_session)
                row = session.query(PlaybackStatus).get(1)
                if not row or not row.playlist_id:
                    pass
                else:
                    status = str(row.status or "").lower()
                    if status == "playing":
                        return int(row.playlist_id)
                    if self._playback_active_marker_exists() or self._playlist_manager_has_active_playback():
                        return int(row.playlist_id)
            except Exception as e:
                self._log_warning(
                    "MPV recover: could not read PlaybackStatus",
                    extra={"error": str(e), "type": type(e).__name__, "action": "mpv_recover"},
                )
            try:
                pm = self._playlist_manager
                pid = getattr(pm, "_active_playlist_id", None)
                if pid:
                    return int(pid)
            except Exception:
                pass
        return None

    def _should_resume_playback_after_boot(self) -> bool:
        return self._resolve_playlist_id_for_recovery() is not None

    def _wait_after_mpv_recover(self) -> None:
        """Brief settle after mpv systemd restart before loadfile (VO/DRM/ytdl hook)."""
        try:
            delay = float((os.getenv("DSIGN_MPV_RECOVER_PLAY_DELAY_SEC") or "2.5").strip())
        except ValueError:
            delay = 2.5
        time.sleep(max(0.5, min(15.0, delay)))

    def _resume_playback_after_boot(self) -> None:
        with self._app_context():
            self._resume_playback_after_boot_impl()

    def _resume_playback_after_boot_impl(self) -> None:
        try:
            playlist_id = self._resolve_playlist_id_for_recovery()
            if playlist_id is None:
                return
            self._wait_before_boot_playlist()
            try:
                settle = float((os.getenv("DSIGN_MPV_POST_INIT_SETTLE_SEC") or "3").strip())
            except ValueError:
                settle = 3.0
            time.sleep(max(0.0, min(15.0, settle)))
            if self.play(playlist_id):
                self._log_info(
                    "Resumed playlist after boot",
                    extra={"playlist_id": playlist_id, "action": "boot_resume"},
                )
            else:
                self._log_warning(
                    "Boot playback resume: play() returned false",
                    extra={"playlist_id": playlist_id, "action": "boot_resume"},
                )
        except Exception as e:
            self._log_warning(
                "Boot playback resume failed",
                extra={"error": str(e), "type": type(e).__name__, "action": "boot_resume"},
            )

    def _transition_to_idle(self):
        """Transition to idle state with logo"""
        max_attempts = 2
        delay = 1.0

        for attempt in range(max_attempts):
            try:
                if self._logo_manager.display_idle_logo():
                    self._log_info(
                        "Successfully transitioned to idle state", 
                        extra={
                            'action': 'transition_to_idle',
                            'attempt': attempt+1
                        }
                    )
                    return
                    
                self._log_warning(
                    "Idle logo attempt failed", 
                    extra={
                        'attempt': attempt+1, 
                        'action': 'transition_to_idle'
                    }
                )
            except Exception as e:
                self._log_error(
                    "Idle transition error", 
                    extra={
                        'attempt': attempt+1, 
                        'action': 'transition_to_idle',
                        'error': str(e),
                        'type': type(e).__name__
                    }
                )
        
            time.sleep(delay)
    
        self._log_error(
            "Could not establish idle state", 
            extra={
                'action': 'transition_to_idle', 
                'status': 'failed',
                'max_attempts': max_attempts
            }
        )
        raise RuntimeError("Could not establish idle state")

    # Делегированные методы
    def play(self, playlist_id: int, *, start_index: int = 0) -> bool:
        """Play specified playlist"""
        try:
            start_time = time.time()
            result = self._playlist_manager.play(playlist_id, start_index=start_index)
            self._log_info(
                "Playing playlist", 
                extra={
                    'playlist_id': playlist_id,
                    'start_index': start_index,
                    'action': 'play',
                    'duration_sec': round(time.time() - start_time, 3),
                    'success': result
                }
            )
            return result
        except Exception as e:
            self._log_error(
                "Error playing playlist", 
                extra={
                    'playlist_id': playlist_id, 
                    'action': 'play',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )
            return False

    def stop(self) -> bool:
        """Stop playback and return to idle state"""
        try:
            start_time = time.time()
            result = self._playlist_manager.stop()
            self._log_info(
                "Playback stopped", 
                extra={
                    'action': 'stop',
                    'duration_sec': round(time.time() - start_time, 3),
                    'success': result
                }
            )
            return result
        except Exception as e:
            self._log_error(
                "Error stopping playback", 
                extra={
                    'action': 'stop',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )
            return False

    def health_check(self) -> Dict[str, Any]:
        """MPV IPC diagnostics for monitoring (no auto vo recovery)."""
        pm = self._playlist_manager
        mgr = self._mpv_manager
        vo_configured = mgr.get_property_light("vo-configured", timeout=2.0)
        checks: Dict[str, Any] = {
            "socket_ok": mgr._check_mpv_socket(timeout=2.0),
            "vo_configured": vo_configured,
            "pause": mgr.get_property_light("pause", timeout=2.0),
            "path": mgr.get_property_light("path", timeout=2.0),
            "playback_session_active": mgr._playback_session_active,
            "last_loaded_media_key": getattr(pm, "_last_loaded_media_key", None),
            "load_in_progress": getattr(pm, "_load_in_progress", False),
            "active_playlist_id": getattr(pm, "_active_playlist_id", None),
        }
        if checks["vo_configured"] is False and mgr._playback_session_active:
            self._log_warning(
                "MPV health: vo not configured during active playback",
                extra={"action": "health_check", **checks},
            )
        return checks

    def get_status(self) -> Dict:
        """Get current playback status"""
        try:
            start_time = time.time()
            status = self._playlist_manager.get_status()
            # This endpoint is frequently polled by the UI (fallback mode / page open).
            # Keep it at DEBUG to avoid flooding journal in idle.
            try:
                self._log_debug(
                    "Retrieved playback status",
                    extra={
                        'action': 'get_status',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
            except Exception:
                # Never fail status because of logging.
                pass
            return status
        except Exception as e:
            self._log_error(
                "Error getting status", 
                extra={
                    'action': 'get_status',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )
            return {'error': str(e)}

    # Остальные методы остаются без изменений, но с добавлением duration_sec в логи
    # ... (остальные методы реализованы аналогично с добавлением временных метрик)

    def wait_for_mpv_ready(self, timeout=30, check_interval=1):
        """Явное ожидание готовности MPV"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._mpv_manager.check_health().get('responsive', False):
                self._log_info(
                    "MPV is ready", 
                    extra={
                        'action': 'wait_for_mpv_ready',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
                return True
            time.sleep(check_interval)
            
        self._log_error(
            "Timeout waiting for MPV to be ready", 
            extra={
                'timeout': timeout, 
                'action': 'wait_for_mpv_ready',
                'duration_sec': round(time.time() - start_time, 3)
            }
        )
        return False
        
    def play_file(self, file_info):
        """Воспроизводит файл с учетом его типа"""
        try:
            start_time = time.time()
            if file_info.get('is_video'):
                self._play_video_full(file_info['filename'])
                self._log_info(
                    "Playing video file", 
                    extra={
                        'filename': file_info['filename'], 
                        'action': 'play_file',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
            else:
                self._show_image(
                    file_info['filename'],
                    duration=file_info['duration']
                )
                self._log_info(
                    "Showing image", 
                    extra={
                        'filename': file_info['filename'], 
                        'duration': file_info['duration'],
                        'action': 'play_file',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
        except Exception as e:
            self._log_error(
                "Error playing file", 
                extra={
                    'filename': file_info.get('filename'), 
                    'action': 'play_file',
                    'error': str(e),
                    'type': type(e).__name__,
                    'duration_sec': round(time.time() - start_time, 3)
                }
            )
            raise
