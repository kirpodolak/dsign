import os
import signal
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
from .recovery_queue import RecoveryJob, RecoveryJobKind, RecoveryQueue
from .wayland_manager import WaylandManager
from .logger import ServiceLogger

class PlaybackService:
    def __init__(
        self,
        upload_folder: str,
        db_session,
        socketio,
        logger: Optional[Union[logging.Logger, ServiceLogger]] = None,
        settings_service=None,
    ):
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

        if settings_service is not None:
            self._playlist_manager.set_settings_service(settings_service)
        
        self._settings_service = settings_service
        self._schedule_service = None
        self._schedule_engine = None
        
        self.logo_manager = LogoManager(
            logger=self.logger,
            socketio=self.socketio,
            upload_folder=self.upload_folder,
            db_session=self.db_session,
            mpv_manager=self._mpv_manager
        )
        
        self._recover_lock = Lock()
        self._recovery_queue = RecoveryQueue()
        self._wayland_manager = WaylandManager(logger=self.logger)
        self._last_socket_identity: Optional[tuple] = None
        self._last_desync_recover_ts: float = 0.0
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
        self._start_playback_desync_watch()

    def set_app(self, app) -> None:
        """Attach Flask app for DB access from background threads (socket watch, boot resume)."""
        self._app = app
        self._app_ready.set()
        pm = getattr(self, "_playlist_manager", None)
        if pm is not None and hasattr(pm, "set_app"):
            pm.set_app(app)
        self._ensure_schedule_engine()

    def set_schedule_service(self, schedule_service) -> None:
        self._schedule_service = schedule_service
        self._ensure_schedule_engine()

    def _ensure_schedule_engine(self) -> None:
        if self._schedule_engine is not None:
            return
        if self._app is None or self._schedule_service is None:
            return
        try:
            from .schedule_engine import ScheduleEngine
            self._schedule_engine = ScheduleEngine(
                self,
                self._schedule_service,
                settings_service=self._settings_service,
                logger=self.logger,
            )
            self._schedule_engine.start()
            self._register_schedule_shutdown_hook()
            self._log_info("ScheduleEngine attached", extra={"action": "schedule_engine_start"})
        except Exception as e:
            self._log_warning(
                "ScheduleEngine start failed",
                extra={"error": str(e), "type": type(e).__name__},
            )

    def _shutdown_join_timeout_sec(self) -> float:
        raw = (os.getenv("DSIGN_SHUTDOWN_JOIN_SEC") or "8").strip()
        try:
            return max(1.0, min(60.0, float(raw)))
        except ValueError:
            return 8.0

    def graceful_shutdown(self, *, signal_num: Optional[int] = None) -> None:
        """
        SIGTERM/SIGINT path: stop schedule, join playback thread, idle logo, MPV IPC, DB session.
        """
        if getattr(self, "_shutdown_started", False):
            return
        self._shutdown_started = True
        extra = {"action": "graceful_shutdown"}
        if signal_num is not None:
            extra["signal"] = int(signal_num)

        try:
            engine = self._schedule_engine
            if engine is not None:
                engine.stop()
                self._log_info("ScheduleEngine stopped on shutdown", extra=extra)
        except Exception as exc:
            self._log_warning(
                "ScheduleEngine stop failed during shutdown",
                extra={**extra, "error": str(exc), "type": type(exc).__name__},
            )

        try:
            self._playlist_manager.stop(
                show_idle_logo=True,
                update_status=True,
                source="shutdown",
                join_timeout=self._shutdown_join_timeout_sec(),
            )
        except Exception as exc:
            self._log_warning(
                "Playback stop failed during shutdown",
                extra={**extra, "error": str(exc), "type": type(exc).__name__},
            )

        try:
            self._mpv_manager.shutdown()
        except Exception as exc:
            self._log_warning(
                "MPV shutdown failed",
                extra={**extra, "error": str(exc), "type": type(exc).__name__},
            )

        try:
            from dsign.extensions import db

            with self._app_context():
                db.session.remove()
        except Exception as exc:
            self._log_warning(
                "DB session cleanup failed during shutdown",
                extra={**extra, "error": str(exc), "type": type(exc).__name__},
            )

        self._log_info("Graceful shutdown completed", extra=extra)

    def _register_schedule_shutdown_hook(self) -> None:
        """SIGTERM/SIGINT → graceful shutdown (schedule, playback thread, idle logo, MPV, DB)."""
        if getattr(self, "_schedule_shutdown_hook_registered", False):
            return
        self._schedule_shutdown_hook_registered = True
        self._shutdown_started = False
        service = self

        def _handler(signum, frame):
            try:
                service.graceful_shutdown(signal_num=int(signum))
            except Exception:
                pass

        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except Exception as exc:
            self._log_warning(
                "Schedule shutdown hook registration failed",
                extra={"error": str(exc), "type": type(exc).__name__},
            )

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

    def _log_debug(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для отладочных логов"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.debug(message, extra=safe_extra)

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
                
                if PlaybackConstants.is_wayland_backend():
                    if not self._wayland_manager.wait_for_compositor(timeout_sec=45.0):
                        raise RuntimeError("Wayland compositor socket not ready")
                    self._wayland_manager.log_status()

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

                try:
                    self._playlist_manager._sync_settings_audio_to_mpv()
                except Exception:
                    pass

                self._ensure_app_wired(timeout=90.0)
                self._ensure_schedule_engine()
                
                Thread(target=self._resume_playback_after_boot, daemon=True).start()
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

    def _start_playback_desync_watch(self) -> None:
        if os.getenv("DSIGN_PLAYBACK_DESYNC_WATCH", "1").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        ):
            return
        Thread(
            target=self._playback_desync_watch_loop,
            name="playback-desync-watch",
            daemon=True,
        ).start()

    def _playback_desync_coalesce_sec(self) -> float:
        try:
            return float((os.getenv("DSIGN_PLAYBACK_DESYNC_COALESCE_SEC") or "60").strip())
        except ValueError:
            return 60.0

    def _playback_desync_watch_interval_sec(self) -> float:
        try:
            return float((os.getenv("DSIGN_PLAYBACK_DESYNC_WATCH_SEC") or "20").strip())
        except ValueError:
            return 20.0

    def _playback_desync_watch_loop(self) -> None:
        interval = max(5.0, min(120.0, self._playback_desync_watch_interval_sec()))
        while True:
            time.sleep(interval)
            try:
                self._maybe_recover_playback_desync()
            except Exception as e:
                self._log_warning(
                    "Playback desync watch raised",
                    extra={
                        "error": str(e),
                        "type": type(e).__name__,
                        "action": "playback_desync_watch",
                    },
                )

    def _clear_stale_playing_status(self) -> None:
        try:
            with self._app_context():
                self._playlist_manager._persist_playback_status(
                    playlist_id=None,
                    status="idle",
                    source="idle",
                    clear_rule=True,
                )
                self._playlist_manager._set_playback_active_marker(False)
                try:
                    self._mpv_manager.set_playback_session_active(False)
                except Exception:
                    pass
        except Exception as e:
            self._log_warning(
                "Failed to clear stale playing status",
                extra={
                    "error": str(e),
                    "type": type(e).__name__,
                    "action": "playback_desync_recover",
                },
            )

    def _maybe_recover_playback_desync(self) -> None:
        if not self._app_ready.is_set():
            return
        if self._recover_lock.locked():
            return
        now = time.monotonic()
        if now - float(self._last_desync_recover_ts or 0.0) < self._playback_desync_coalesce_sec():
            return
        # Plan under a short DB context; call play() only after session.remove so a
        # slow network loadfile cannot hold a QueuePool checkout (login starvation).
        playlist_id = None
        resume_index = 0
        resume_source = "manual"
        resume_rule_id = None
        with self._app_context():
            try:
                snap = self._playlist_manager._remote_playback_snapshot()
                if str(snap.get("db_status") or "").lower() != "playing":
                    return
                if snap.get("thread_alive"):
                    return
                playlist_id = snap.get("db_playlist_id")
                if not playlist_id:
                    return
                if snap.get("mpv_idle") is not True and self._playlist_manager._mpv_has_active_media():
                    return
                self._last_desync_recover_ts = now
                try:
                    resume_index = self._playlist_manager.get_resume_start_index(advance=False)
                except Exception:
                    resume_index = 0
                _pid, resume_source, resume_rule_id = self._resolve_playback_resume_context()
            finally:
                try:
                    from ..extensions import db

                    db.session.remove()
                except Exception:
                    pass

        if not playlist_id:
            return
        self._log_warning(
            "Playback desync: DB playing but slideshow thread dead and mpv idle; resuming",
            extra={
                "playlist_id": playlist_id,
                "start_index": resume_index,
                "source": resume_source,
                "rule_id": resume_rule_id,
                "action": "playback_desync_recover",
            },
        )
        ok = False
        try:
            ok = bool(
                self.play(
                    int(playlist_id),
                    start_index=resume_index,
                    preserve_stall_tracking=True,
                    source=resume_source,
                    rule_id=resume_rule_id,
                )
            )
        except Exception as e:
            self._log_warning(
                "Playback desync resume raised",
                extra={
                    "playlist_id": playlist_id,
                    "error": str(e),
                    "type": type(e).__name__,
                    "action": "playback_desync_recover",
                },
            )
        if not ok:
            self._clear_stale_playing_status()

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
                try:
                    advance = self._playlist_manager.consume_stall_recovery_advance()
                    with self._app_context():
                        self._enqueue_mpv_recovery(resume_advance=advance)
                except Exception as e:
                    self._log_warning(
                        "MPV socket watch recovery queue failed",
                        extra={"error": str(e), "type": type(e).__name__, "action": "mpv_socket_watch"},
                    )
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

    def _enqueue_mpv_recovery(
        self,
        *,
        restart_playlist: Optional[bool] = None,
        resume_advance: bool = False,
    ) -> bool:
        job = RecoveryJob(
            RecoveryJobKind.MPV_SYSTEMD,
            kwargs={
                "restart_playlist": restart_playlist,
                "resume_advance": bool(resume_advance),
            },
        )
        if self._recovery_queue.enqueue(job):
            self._log_info(
                "MPV recovery queued",
                extra={
                    "action": "mpv_recover",
                    "queue_len": len(self._recovery_queue),
                    "resume_advance": bool(resume_advance),
                },
            )
            return True
        self._log_warning(
            "MPV recovery queue full; dropping request",
            extra={"action": "mpv_recover", "queue_len": len(self._recovery_queue)},
        )
        return False

    def _process_next_queued_recovery(self) -> None:
        job = self._recovery_queue.pop()
        if job is None:
            return
        try:
            with self._app_context():
                if job.kind == RecoveryJobKind.MPV_SYSTEMD:
                    self._recover_after_mpv_systemd_restart_impl(
                        restart_playlist=job.kwargs.get("restart_playlist"),
                        resume_advance=bool(job.kwargs.get("resume_advance", False)),
                    )
                elif job.kind == RecoveryJobKind.SLIDESHOW_CRASH:
                    self._resume_slideshow_after_crash_impl()
        except Exception as exc:
            self._log_warning(
                "Queued recovery dispatch failed",
                extra={
                    "kind": job.kind.value,
                    "error": str(exc),
                    "type": type(exc).__name__,
                    "action": "recovery_queue",
                },
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
                self._resume_slideshow_after_crash_impl()
        except Exception as e:
            self._log_warning(
                "Slideshow crash recovery failed",
                extra={"error": str(e), "type": type(e).__name__, "action": "slideshow_recover"},
            )

    def _resume_slideshow_after_crash_impl(self) -> None:
        if not self._recover_lock.acquire(blocking=False):
            if self._recovery_queue.enqueue(RecoveryJob(RecoveryJobKind.SLIDESHOW_CRASH)):
                self._log_info(
                    "Slideshow recovery queued",
                    extra={"action": "slideshow_recover", "queue_len": len(self._recovery_queue)},
                )
            return
        try:
            playlist_id = self._resolve_playlist_id_for_recovery()
            if playlist_id is None:
                return
            resume_index = self._playlist_manager.get_resume_start_index(advance=False)
            _pid, resume_source, resume_rule_id = self._resolve_playback_resume_context()
            ok = bool(
                self._playlist_manager.play(
                    playlist_id,
                    start_index=resume_index,
                    source=resume_source,
                    rule_id=resume_rule_id,
                )
            )
            self._log_warning(
                "Resumed playlist after slideshow thread crash",
                extra={
                    "playlist_id": playlist_id,
                    "start_index": resume_index,
                    "source": resume_source,
                    "rule_id": resume_rule_id,
                    "ok": ok,
                    "action": "slideshow_recover",
                },
            )
        finally:
            self._recover_lock.release()
            self._process_next_queued_recovery()

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
            self._enqueue_mpv_recovery(
                restart_playlist=restart_playlist,
                resume_advance=resume_advance,
            )
            return False
        try:
            return self._recover_after_mpv_systemd_restart_body(
                restart_playlist=restart_playlist,
                resume_advance=resume_advance,
            )
        finally:
            self._recover_lock.release()
            self._process_next_queued_recovery()

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
                    if resume_advance:
                        resume_index = self._playlist_manager.get_resume_start_index(
                            advance=True
                        )
                    else:
                        resume_index = (
                            self._playlist_manager.get_resume_start_index_for_hung_recovery()
                        )
                except Exception:
                    resume_index = 0
        try:
            self._playlist_manager.stop(
                show_idle_logo=False,
                update_status=False,
                preserve_stall_tracking=True,
                preserve_loop_position=True,
            )
        except Exception:
            pass
        self._playlist_manager.mark_post_mpv_restart()
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
        try:
            self._playlist_manager._logo_manager.clear_wayland_audio_vo_state()
            self._playlist_manager._sync_settings_audio_to_mpv()
        except Exception:
            pass
        self._wait_after_mpv_recover()
        self._last_socket_identity = self._mpv_socket_identity()
        if playlist_id is not None:
            _pid, resume_source, resume_rule_id = self._resolve_playback_resume_context()
            ok = False
            for attempt in range(2):
                try:
                    ok = bool(
                        self.play(
                            playlist_id,
                            start_index=resume_index,
                            preserve_stall_tracking=True,
                            source=resume_source,
                            rule_id=resume_rule_id,
                        )
                    )
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
                            "source": resume_source,
                            "rule_id": resume_rule_id,
                            "last_good_media_key": (
                                self._playlist_manager.get_network_playback_health().get(
                                    "last_good_media_key"
                                )
                            ),
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
            self._clear_stale_playing_status()
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
                ["sudo", "-n", "/usr/bin/systemctl", "start", "dsign-show-startup-ip.service"],
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
                    if status == "stopped":
                        return None
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

    def _resolve_playback_resume_context(
        self,
    ) -> tuple[Optional[int], str, Optional[int]]:
        """Return (playlist_id, source, rule_id) for recovery resume — preserve schedule."""
        playlist_id = self._resolve_playlist_id_for_recovery()
        source = "manual"
        rule_id: Optional[int] = None
        with self._app_context():
            try:
                from ..models import PlaybackStatus

                session = getattr(self.db_session, "session", self.db_session)
                row = session.query(PlaybackStatus).get(1)
                if row is not None:
                    source = str(row.source or "manual")
                    if row.rule_id is not None:
                        rule_id = int(row.rule_id)
            except Exception as e:
                self._log_warning(
                    "Could not read playback source for recovery",
                    extra={
                        "error": str(e),
                        "type": type(e).__name__,
                        "action": "playback_resume_context",
                    },
                )
        return playlist_id, source, rule_id

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
            if PlaybackConstants.is_wayland_backend():
                if not self._wayland_manager.wait_for_compositor(timeout_sec=60.0):
                    self._log_warning(
                        "Compositor not ready for boot resume; continuing",
                        extra={"action": "boot_resume"},
                    )
            self._wait_before_boot_playlist()
            try:
                settle = float((os.getenv("DSIGN_MPV_POST_INIT_SETTLE_SEC") or "3").strip())
            except ValueError:
                settle = 3.0
            time.sleep(max(0.0, min(15.0, settle)))
            engine = self._schedule_engine
            if engine is not None:
                engine.evaluate_and_apply(ignore_manual=True)
                self._log_info("Boot schedule evaluate completed", extra={"action": "boot_resume"})
            else:
                self._transition_to_idle()
        except Exception as e:
            self._log_warning(
                "Boot playback resume failed",
                extra={"error": str(e), "type": type(e).__name__, "action": "boot_resume"},
            )

    def _transition_to_idle(self):
        """Transition to idle state with logo (Wayland: imv underneath; DRM: MPV loadfile)."""
        max_attempts = 2
        delay = 1.0
        wayland = PlaybackConstants.is_wayland_backend()

        for attempt in range(max_attempts):
            try:
                if self._logo_manager.display_idle_logo():
                    self._log_info(
                        "Successfully transitioned to idle state",
                        extra={
                            "action": "transition_to_idle",
                            "attempt": attempt + 1,
                            "backend": PlaybackConstants.mpv_backend_label(),
                        },
                    )
                    return

                self._log_warning(
                    "Idle logo attempt failed",
                    extra={
                        "attempt": attempt + 1,
                        "action": "transition_to_idle",
                    },
                )
            except Exception as e:
                self._log_error(
                    "Idle transition error",
                    extra={
                        "attempt": attempt + 1,
                        "action": "transition_to_idle",
                        "error": str(e),
                        "type": type(e).__name__,
                    },
                )

            time.sleep(delay)

        if wayland:
            self._log_warning(
                "MPV stop failed but logo is visible via compositor",
                extra={"action": "transition_to_idle", "status": "degraded"},
            )
            return

        self._log_error(
            "Could not establish idle state",
            extra={
                "action": "transition_to_idle",
                "status": "failed",
                "max_attempts": max_attempts,
            },
        )
        raise RuntimeError("Could not establish idle state")

    # Делегированные методы
    def play(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
        source: str = "manual",
        rule_id: Optional[int] = None,
    ) -> bool:
        """Play specified playlist"""
        try:
            start_time = time.time()
            result = self._playlist_manager.play(
                playlist_id,
                start_index=start_index,
                preserve_stall_tracking=preserve_stall_tracking,
                source=source,
                rule_id=rule_id,
            )
            self._log_info(
                "Playing playlist", 
                extra={
                    'playlist_id': playlist_id,
                    'start_index': start_index,
                    'source': source,
                    'rule_id': rule_id,
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

    def play_override(
        self,
        playlist_id: int,
        *,
        return_to_previous: bool = True,
        start_index: int = 0,
    ) -> Dict[str, Any]:
        """Emergency override: play playlist once, then resume previous if requested."""
        try:
            start_time = time.time()
            with self._app_context():
                from ..models import PlaybackStatus
                session = getattr(self.db_session, "session", self.db_session)
                row = session.query(PlaybackStatus).get(1)
                if row is not None:
                    row.previous_source = row.source or "idle"
                    row.previous_rule_id = row.rule_id
                    row.previous_playlist_id = row.playlist_id
                    session.add(row)
                    session.commit()
            result = self._playlist_manager.play_override(
                playlist_id,
                return_to_previous=return_to_previous,
                start_index=start_index,
            )
            self._log_info(
                "Playback override",
                extra={
                    "playlist_id": playlist_id,
                    "return_to_previous": return_to_previous,
                    "action": "play_override",
                    "duration_sec": round(time.time() - start_time, 3),
                    **(result if isinstance(result, dict) else {}),
                },
            )
            return result if isinstance(result, dict) else {"success": bool(result)}
        except Exception as e:
            self._log_error(
                "Error in playback override",
                extra={
                    "playlist_id": playlist_id,
                    "action": "play_override",
                    "error": str(e),
                    "type": type(e).__name__,
                },
            )
            return {"success": False, "error": str(e)}

    def handle_override_return(self) -> None:
        """Resume playback after override single-pass ends (§6.3)."""
        with self._app_context():
            from ..models import PlaybackStatus
            session = getattr(self.db_session, "session", self.db_session)
            row = session.query(PlaybackStatus).get(1)
            if row is None:
                self.stop(source="schedule")
                return

            prev = row.previous_source
            if prev == "schedule":
                if self._schedule_engine is not None:
                    # Do not block the override-return thread on loadfile.
                    self.enqueue_schedule_evaluate(ignore_manual=False)
                else:
                    self.stop(source="schedule")
            elif prev == "manual" and row.previous_playlist_id:
                self.enqueue_play(int(row.previous_playlist_id), source="manual")
            else:
                self.stop(source="schedule")

    def enqueue_schedule_evaluate(self, *, ignore_manual: bool = False) -> bool:
        """Run ScheduleEngine.evaluate_and_apply on a daemon thread (never block HTTP)."""
        engine = self._schedule_engine
        if engine is None or not hasattr(engine, "evaluate_and_apply"):
            return False
        app = self._app

        def _run() -> None:
            try:
                if app is not None:
                    with app.app_context():
                        engine.evaluate_and_apply(ignore_manual=ignore_manual)
                else:
                    engine.evaluate_and_apply(ignore_manual=ignore_manual)
            except Exception as exc:
                self._log_error(
                    "Async schedule evaluate failed",
                    extra={
                        "error": str(exc),
                        "type": type(exc).__name__,
                        "action": "schedule_evaluate_async",
                        "ignore_manual": bool(ignore_manual),
                    },
                )

        Thread(target=_run, name="schedule-evaluate", daemon=True).start()
        return True

    def enqueue_play(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
        source: str = "manual",
        rule_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Start play() off the HTTP worker — loadfile/ytdl must not freeze the UI."""
        app = self._app
        pid = int(playlist_id)

        def _run() -> None:
            try:
                if app is not None:
                    with app.app_context():
                        self.play(
                            pid,
                            start_index=start_index,
                            preserve_stall_tracking=preserve_stall_tracking,
                            source=source,
                            rule_id=rule_id,
                        )
                else:
                    self.play(
                        pid,
                        start_index=start_index,
                        preserve_stall_tracking=preserve_stall_tracking,
                        source=source,
                        rule_id=rule_id,
                    )
            except Exception as exc:
                self._log_error(
                    "Async play failed",
                    extra={
                        "playlist_id": pid,
                        "source": source,
                        "error": str(exc),
                        "type": type(exc).__name__,
                        "action": "play_async",
                    },
                )

        Thread(target=_run, name=f"playback-play-{pid}", daemon=True).start()
        return {
            "accepted": True,
            "playlist_id": pid,
            "source": source,
            "rule_id": rule_id,
            "start_index": int(start_index or 0),
        }

    def return_to_schedule(self) -> bool:
        """Clear manual lock and apply current schedule slot (D2.2).

        Evaluate/play runs asynchronously so the UI request cannot hang on loadfile.
        """
        with self._app_context():
            from ..models import PlaybackStatus
            session = getattr(self.db_session, "session", self.db_session)
            row = session.query(PlaybackStatus).get(1)
            if row is not None and (row.source or "idle") == "manual":
                row.source = "idle"
                session.add(row)
                session.commit()
            if self._schedule_engine is None:
                return False
        return self.enqueue_schedule_evaluate(ignore_manual=True)

    def remote_pause(self, paused: Optional[bool] = None) -> Dict[str, Any]:
        """Pause or resume current playlist playback via MPV."""
        try:
            return self._playlist_manager.remote_pause(paused=paused)
        except Exception as e:
            self._log_error(
                "Error pausing playback",
                extra={"action": "remote_pause", "error": str(e)},
            )
            return {"success": False, "error": str(e)}

    def remote_seek(self, position_sec: float) -> Dict[str, Any]:
        """Seek within the current media item."""
        try:
            return self._playlist_manager.remote_seek(position_sec)
        except Exception as e:
            self._log_error(
                "Error seeking playback",
                extra={"action": "remote_seek", "error": str(e)},
            )
            return {"success": False, "error": str(e)}

    def remote_skip(self, direction: str = "next") -> Dict[str, Any]:
        """Skip to next/previous item in the active playlist loop."""
        try:
            return self._playlist_manager.remote_skip(direction=direction)
        except Exception as e:
            self._log_error(
                "Error skipping playback item",
                extra={"action": "remote_skip", "error": str(e)},
            )
            return {"success": False, "error": str(e)}

    def stop(self, *, source: str = "manual") -> bool:
        """Stop playback and return to idle state"""
        try:
            start_time = time.time()
            result = self._playlist_manager.stop(source=source)
            self._log_info(
                "Playback stopped", 
                extra={
                    'action': 'stop',
                    'source': source,
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
        try:
            checks["network_playback"] = pm.get_network_playback_health()
        except Exception:
            checks["network_playback"] = {}
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
            nxt = None
            if self._schedule_service is not None:
                try:
                    with self._app_context():
                        nxt = self._schedule_service.find_next_rule()
                except Exception:
                    nxt = None
            status["schedule"] = {
                "source": status.get("source") or "idle",
                "active_rule_id": status.get("rule_id"),
                "next_rule_at": nxt.get("at") if isinstance(nxt, dict) else None,
                "next_rule_id": (
                    (nxt.get("rule") or {}).get("id") if isinstance(nxt, dict) else None
                ),
            }
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
