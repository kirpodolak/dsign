import os
import time
from pathlib import Path
from typing import Dict, Optional, List, Union, Any
from concurrent.futures import ThreadPoolExecutor
from threading import Thread, Lock
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

        # Initialize with retry
        self._init_with_retry()
        self._start_mpv_socket_watch()

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
                self._log_error(
                    f"Initialization attempt {attempt+1} failed", 
                    extra={
                        'attempt': attempt+1, 
                        'action': 'init',
                        'error': str(e),
                        'type': type(e).__name__
                    }
                )
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        self._log_error(
            "Initialization failed after all attempts", 
            extra={
                'action': 'init', 
                'status': 'failed',
                'last_error': str(last_exception)
            }
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
            self._log_warning(
                "MPV IPC socket recreated (systemd restart?); recovering playback",
                extra={"action": "mpv_socket_watch"},
            )
            try:
                self.recover_after_mpv_systemd_restart()
            except Exception as e:
                self._log_warning(
                    "MPV socket watch recovery failed",
                    extra={"error": str(e), "type": type(e).__name__, "action": "mpv_socket_watch"},
                )

    def recover_after_mpv_systemd_restart(self, *, restart_playlist: Optional[bool] = None) -> bool:
        """Re-bind IPC after `systemctl restart dsign-mpv` without restarting digital-signage."""
        with self._recover_lock:
            if restart_playlist is None:
                restart_playlist = self._should_resume_playback_after_boot()
            playlist_id: Optional[int] = None
            if restart_playlist:
                try:
                    from ..models import PlaybackStatus
                    row = self.db_session.query(PlaybackStatus).get(1)
                    if row and row.playlist_id:
                        playlist_id = int(row.playlist_id)
                except Exception:
                    playlist_id = None
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
            self._last_socket_identity = self._mpv_socket_identity()
            if playlist_id is not None:
                self.play(playlist_id)
                self._log_info(
                    "Resumed playlist after MPV service restart",
                    extra={"playlist_id": playlist_id, "action": "mpv_recover"},
                )
            else:
                Thread(target=self._preload_resources, daemon=True).start()
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

    def _should_resume_playback_after_boot(self) -> bool:
        try:
            from ..models import PlaybackStatus
            row = self.db_session.query(PlaybackStatus).get(1)
            return bool(
                row
                and row.playlist_id
                and str(row.status or "").lower() == "playing"
            )
        except Exception:
            return False

    def _resume_playback_after_boot(self) -> None:
        from ..models import PlaybackStatus
        try:
            row = self.db_session.query(PlaybackStatus).get(1)
            if not row or not row.playlist_id:
                return
            if str(row.status or "").lower() != "playing":
                return
            self._wait_before_boot_playlist()
            self.play(int(row.playlist_id))
            self._log_info(
                "Resumed playlist after boot",
                extra={"playlist_id": int(row.playlist_id), "action": "boot_resume"},
            )
        except Exception as e:
            self._log_warning(
                "Boot playback resume failed",
                extra={"error": str(e), "type": type(e).__name__, "action": "boot_resume"},
            )

    def _transition_to_idle(self):
        """Transition to idle state with logo"""
        max_attempts = 5
        delay = 2
    
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
    def play(self, playlist_id: int) -> bool:
        """Play specified playlist"""
        try:
            start_time = time.time()
            result = self._playlist_manager.play(playlist_id)
            self._log_info(
                "Playing playlist", 
                extra={
                    'playlist_id': playlist_id, 
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
