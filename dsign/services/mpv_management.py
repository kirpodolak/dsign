import os
import shutil
import socket
import time
import subprocess
from threading import Lock
from typing import Dict, Optional, Any, List
from pathlib import Path

from .playback_constants import PlaybackConstants
from .logger import ServiceLogger
from .mpv_ipc_session import MPVIPCClosedError, MPVIPCTimeoutError, MpvJsonIpcSession


def _is_ipc_transport_error(exc: BaseException) -> bool:
    """True when the IPC session died (mpv restart/crash) rather than a logical mpv error."""
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, MPVIPCClosedError)):
        return True
    if isinstance(exc, OSError):
        errno = getattr(exc, "errno", None)
        # Linux: ECONNRESET 104, EPIPE 32
        if errno in (104, 32):
            return True
    return False


def _ipc_error_should_restart_mpv(exc: BaseException) -> bool:
    """Transport drop or hung IPC (no reply) — systemd restart is the practical recovery."""
    return _is_ipc_transport_error(exc) or isinstance(exc, MPVIPCTimeoutError)


class MPVManager:
    def __init__(self, 
                 logger: ServiceLogger, 
                 socketio: Any, 
                 upload_folder: str, 
                 mpv_socket: Optional[str] = None):
        """
        Инициализация менеджера MPV с улучшенным логированием
        
        Args:
            logger: Экземпляр ServiceLogger
            socketio: Объект SocketIO
            upload_folder: Директория для загрузки файлов
            mpv_socket: Путь к сокету (по умолчанию из PlaybackConstants)
        """
        self.logger = logger
        self.socketio = socketio
        self.mpv_socket = mpv_socket or PlaybackConstants.SOCKET_PATH
        self.upload_folder = upload_folder
        self._ipc_lock = Lock()
        self._ipc_session: Optional[MpvJsonIpcSession] = None
        self._mpv_restart_coalesce_lock = Lock()
        self._last_mpv_restart_attempt_ts = 0.0
        self._current_settings = {}
        self._mpv_ready = False
        self._managed_by_systemd = True
        self._playback_session_active = False
        self._playback_ipc_fail_streak = 0
        self._playback_ipc_fail_lock = Lock()

        # Логирование инициализации
        self.logger.info(
            "Initializing MPVManager",
            extra={
                "operation": "mpv_init",
                "socket_path": self.mpv_socket,
                "upload_folder": self.upload_folder,
                "systemd_managed": True
            }
        )
        
        self._last_known_state = {
            'paused': True,
            'volume': 100,
            'mute': False
        }

    def _cache_mpv_state(self):
        """Кеширует важные параметры MPV"""
        props = ["pause", "volume", "mute"]
        for prop in props:
            resp = self._send_command({
                "command": ["get_property", prop]
            })
            if resp and "data" in resp:
                self._last_known_state[prop] = resp["data"]
    
    def _restore_mpv_state(self):
        """Восстанавливает кешированное состояние"""
        for prop, value in self._last_known_state.items():
            self._send_ipc_command({
                "command": ["set", prop, value]
            })
    
    def _log_operation(self, operation: str, status: str, details: Dict[str, Any] = None):
        """Унифицированное логирование операций"""
        log_data = {
            "operation": operation,
            "status": status,
            **({"details": details} if details else {})
        }
        self.logger.info(
            f"{operation} - {status}",
            extra=log_data
        )

    def set_playback_session_active(self, active: bool) -> None:
        """Playlist thread sets this to reduce MPV systemd restarts during stream transitions."""
        self._playback_session_active = bool(active)
        if not active:
            self._reset_playback_ipc_fail_streak()


    def _ipc_lock_timeout_sec(self, lock_wait: Optional[float] = None) -> float:
        if lock_wait is not None:
            return max(0.1, min(60.0, float(lock_wait)))
        try:
            sec = float((os.getenv("DSIGN_MPV_IPC_LOCK_TIMEOUT_SEC") or "6").strip())
        except ValueError:
            sec = 6.0
        return max(0.5, min(30.0, sec))

    def _acquire_ipc_lock(self, *, lock_wait: Optional[float] = None) -> bool:
        return self._ipc_lock.acquire(timeout=self._ipc_lock_timeout_sec(lock_wait))

    def _release_ipc_lock(self) -> None:
        try:
            self._ipc_lock.release()
        except RuntimeError:
            pass

    def _reset_playback_ipc_fail_streak(self) -> None:
        with self._playback_ipc_fail_lock:
            self._playback_ipc_fail_streak = 0

    def _playback_hung_restart_threshold(self) -> int:
        try:
            n = int((os.getenv("DSIGN_MPV_PLAYBACK_HUNG_RESTART_AFTER") or "12").strip())
        except ValueError:
            n = 12
        return max(4, min(60, n))

    def _note_playback_ipc_failure(self, exc: BaseException) -> None:
        if not self._playback_session_active:
            return
        if not (
            isinstance(exc, MPVIPCTimeoutError)
            or isinstance(exc, MPVIPCClosedError)
            or _is_ipc_transport_error(exc)
        ):
            return
        with self._playback_ipc_fail_lock:
            self._playback_ipc_fail_streak += 1
            streak = self._playback_ipc_fail_streak
        if streak < self._playback_hung_restart_threshold():
            return
        self.logger.error(
            "MPV appears hung during playlist (IPC failures); forcing systemd restart",
            extra={
                "operation": "PlaybackHungRecovery",
                "streak": streak,
                "error": str(exc),
                "type": type(exc).__name__,
            },
        )
        with self._playback_ipc_fail_lock:
            self._playback_ipc_fail_streak = 0
        if self._restart_systemd_service_if_needed():
            self._wait_for_socket(timeout=15.0)
            self._reset_ipc_session()

    def _restart_during_playback_allowed(self) -> bool:
        v = os.getenv("DSIGN_MPV_RESTART_DURING_PLAYBACK", "0").strip().lower()
        return v in ("1", "true", "yes", "on")

    def _socket_recover_wait_sec(self) -> float:
        try:
            sec = float((os.getenv("DSIGN_MPV_SOCKET_RECOVER_SEC") or "12").strip())
        except ValueError:
            sec = 12.0
        return max(2.0, min(60.0, sec))

    def _try_recover_socket_without_restart(self) -> bool:
        """Wait for IPC socket while mpv is busy (ytdl/HLS) without restarting systemd."""
        deadline = time.time() + self._socket_recover_wait_sec()
        while time.time() < deadline:
            if self._check_mpv_socket(timeout=0.5):
                return True
            time.sleep(0.25)
        return False

    def _should_restart_mpv_service(self) -> bool:
        if self._playback_session_active and not self._restart_during_playback_allowed():
            return False
        return True

    def _restart_systemd_service_if_needed(self) -> bool:
        if not self._should_restart_mpv_service():
            self.logger.warning(
                "Skipping MPV systemd restart during active playlist playback",
                extra={"operation": "SystemdServiceRestart"},
            )
            return self._try_recover_socket_without_restart()
        return self._restart_systemd_service()

    def _ipc_failure_should_systemd_restart(self, exc: BaseException) -> bool:
        """During playlist playback avoid systemd restart on slow IPC (mpv busy opening streams)."""
        if self._playback_session_active and not self._restart_during_playback_allowed():
            if isinstance(exc, MPVIPCTimeoutError) or _is_ipc_transport_error(exc):
                return False
        return _ipc_error_should_restart_mpv(exc)

    def _check_systemd_service(self) -> bool:
        """Проверка статуса systemd сервиса"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "dsign-mpv.service"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5.0
            )
            is_active = result.stdout.strip() == "active"
            
            self._log_operation(
                "SystemdServiceCheck",
                "active" if is_active else "inactive",
                {"output": result.stdout.strip()}
            )
            return is_active
            
        except subprocess.TimeoutExpired:
            self.logger.error(
                "Systemd check timeout",
                extra={"operation": "SystemdServiceCheck"}
            )
            return False
            
        except subprocess.CalledProcessError as e:
            self.logger.error(
                "Systemd check failed",
                extra={
                    "operation": "SystemdServiceCheck",
                    "error": str(e),
                    "stderr": e.stderr.strip()
                }
            )
            return False
            
        except Exception as e:
            self.logger.error(
                "Systemd check unexpected error",
                extra={
                    "operation": "SystemdServiceCheck",
                    "error": str(e),
                    "type": type(e).__name__
                }
            )
            return False

    def _restart_systemd_service(self) -> bool:
        """
        Перезапуск systemd-сервиса MPV.

        Процесс digital-signage работает от пользователя dsign; прямой вызов systemctl
        требует интерактивной политики/polkit. Используем sudo -n при наличии sudoers NOPASSWD.
        """
        try:
            start_time = time.time()
            systemctl = shutil.which("systemctl") or "/bin/systemctl"
            result = subprocess.run(
                ["sudo", "-n", systemctl, "restart", "dsign-mpv.service"],
                check=True,
                capture_output=True,
                text=True,
                timeout=25.0,
            )
            duration = time.time() - start_time
            
            self._log_operation(
                "SystemdServiceRestart",
                "success",
                {
                    "duration_sec": round(duration, 3),
                    "output": result.stdout.strip()
                }
            )
            return True
            
        except subprocess.TimeoutExpired:
            self.logger.error(
                "Systemd restart timeout",
                extra={"operation": "SystemdServiceRestart"}
            )
            return False
            
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or e.stdout or "").strip() or str(e)
            msg_l = msg.lower()
            if (
                "a terminal is required" in msg_l
                or "password is required" in msg_l
                or "interactive authentication is required" in msg_l
            ):
                msg = (
                    "sudoers not configured (NOPASSWD) for: sudo systemctl restart dsign-mpv.service"
                )
            self.logger.error(
                "Systemd restart failed",
                extra={
                    "operation": "SystemdServiceRestart",
                    "error": str(e),
                    "stderr": msg,
                }
            )
            return False
            
        except Exception as e:
            self.logger.error(
                "Systemd restart unexpected error",
                extra={
                    "operation": "SystemdServiceRestart",
                    "error": str(e),
                    "type": type(e).__name__
                }
            )
            return False

    def _check_mpv_socket(self, timeout=5) -> bool:
        """Проверка доступности сокета MPV"""
        end_time = time.time() + timeout
        while time.time() < end_time:
            if os.path.exists(self.mpv_socket):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(1.0)
                        s.connect(self.mpv_socket)
                        return True
                except (ConnectionRefusedError, socket.error):
                    time.sleep(0.1)
        return False

    def _wait_for_socket(self, timeout: float = 10.0) -> bool:
        """Ожидание сокета"""
        self._log_operation(
            "SocketWait",
            "started",
            {"timeout": timeout, "socket_path": self.mpv_socket}
        )
        
        if not self._check_mpv_socket(timeout=1.0):
            if not self._check_systemd_service():
                self.logger.warning(
                    "MPV service inactive, attempting restart",
                    extra={"operation": "SocketWait"},
                )
                if not self._restart_systemd_service_if_needed():
                    return False

        start_time = time.time()
        last_status_time = start_time
        
        while time.time() - start_time < timeout:
            current_time = time.time()
            
            if current_time - last_status_time >= 2.0:
                self._log_operation(
                    "SocketWait",
                    "checking",
                    {"elapsed_sec": round(current_time - start_time, 1)}
                )
                last_status_time = current_time
            
            if self._check_mpv_socket(timeout=1.0):
                self._log_operation(
                    "SocketTest",
                    "success",
                    {"elapsed_sec": round(time.time() - start_time, 3)}
                )
                return True
            
            time.sleep(0.2)
        
        self.logger.error(
            "Socket wait timeout expired",
            extra={
                "operation": "SocketWait",
                "timeout": timeout,
                "elapsed_sec": round(time.time() - start_time, 3)
            }
        )
        return False

    def _get_ipc_session(self) -> MpvJsonIpcSession:
        if self._ipc_session is None:
            self._ipc_session = MpvJsonIpcSession(self.mpv_socket, logger=self.logger)
        return self._ipc_session

    def _reset_ipc_session(self) -> None:
        """Drop IPC socket state after mpv restart or transport failure (next command reconnects)."""
        if self._ipc_session is not None:
            try:
                self._ipc_session.reset()
            except Exception:
                pass

    @staticmethod
    def _normalize_get_property_batch_reply(
        result: Dict[str, Any], ipc_request_id: int
    ) -> Dict[str, Any]:
        """Match single-command normalization for get_property (unavailable → success, data None)."""
        err = result.get("error")
        if err == "success":
            return result
        if err in ("property unavailable", "property not found"):
            return {
                "request_id": result.get("request_id", ipc_request_id),
                "error": "success",
                "data": None,
            }
        return result

    def get_properties_snapshot(
        self,
        names: List[str],
        *,
        timeout: float = 3.0,
        lock_wait: Optional[float] = None,
    ) -> Dict[str, Optional[Any]]:
        """
        Multiple get_property in one IPC round-trip (ordered, de-duplicated keys).

        Returns map property_name -> data (or None if unavailable / error). No per-property logging.
        """
        ordered: List[str] = []
        seen: set[str] = set()
        for n in names:
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        if not ordered:
            return {}

        delays_transport = getattr(
            PlaybackConstants,
            "RETRY_DELAY_TRANSPORT_SEC",
            (0.15, 0.35, 0.75),
        )
        if not isinstance(delays_transport, (list, tuple)):
            delays_transport = (0.15, 0.35, 0.75)

        def _retry_sleep_after_failure(exc: BaseException, attempt_idx: int) -> None:
            if attempt_idx >= PlaybackConstants.MAX_RETRIES - 1:
                return
            if _ipc_error_should_restart_mpv(exc):
                i = min(attempt_idx, len(delays_transport) - 1)
                try:
                    d = float(delays_transport[i])
                except (TypeError, ValueError, IndexError):
                    d = 0.25
                time.sleep(max(0.0, d))
            else:
                time.sleep(PlaybackConstants.RETRY_DELAY)

        def _maybe_restart_mpv_batch(
            *, reason: str, attempt_num: int, ipc_request_id: int
        ) -> None:
            window = float(os.getenv("DSIGN_MPV_RESTART_COALESCE_SEC", "8") or 8)
            if window < 0:
                window = 0.0
            now = time.time()
            with self._mpv_restart_coalesce_lock:
                if window > 0 and (now - self._last_mpv_restart_attempt_ts) < window:
                    return
                self._last_mpv_restart_attempt_ts = now
            self.logger.warning(
                "MPV IPC failure; attempting systemd restart",
                extra={
                    "operation": "MPVCommandBatch",
                    "attempt": attempt_num,
                    "request_id": ipc_request_id,
                    "reason": reason,
                    "properties": ",".join(ordered[:12])
                    + ("..." if len(ordered) > 12 else ""),
                },
            )
            ok = self._restart_systemd_service_if_needed() and self._wait_for_socket(timeout=12.0)
            if ok:
                self._reset_ipc_session()
            if not ok:
                self.logger.error(
                    "MPV systemd restart after IPC batch failure did not restore socket",
                    extra={
                        "operation": "MPVCommandBatch",
                        "attempt": attempt_num,
                    },
                )

        first_rid = 0
        for attempt in range(PlaybackConstants.MAX_RETRIES):
            base_rid = int(time.time() * 1_000_000) & 0x7FFFFFFF
            ids: List[int] = []
            items: List[tuple[int, Dict[str, Any]]] = []
            for idx, pname in enumerate(ordered):
                ipc_request_id = (base_rid + idx * 7919 + ((idx & 31) << 20)) & 0x7FFFFFFF
                ipc_request_id = max(1, ipc_request_id)
                ids.append(ipc_request_id)
                items.append((ipc_request_id, {"command": ["get_property", pname]}))
            first_rid = ids[0] if ids else 0
            try:
                if not self._acquire_ipc_lock(lock_wait=lock_wait):
                    self.logger.debug(
                        "IPC lock busy; batch snapshot skipped",
                        extra={"operation": "MPVCommandBatch"},
                    )
                    raise MPVIPCTimeoutError("IPC lock busy (batch)")
                try:
                    sess = self._get_ipc_session()
                    raw_results = sess.commands_batch(items, timeout=float(timeout))
                finally:
                    self._release_ipc_lock()

                out: Dict[str, Optional[Any]] = {}
                for pname, ipc_request_id, raw in zip(ordered, ids, raw_results):
                    norm = self._normalize_get_property_batch_reply(raw, ipc_request_id)
                    if norm.get("error") != "success":
                        out[pname] = None
                        continue
                    out[pname] = norm.get("data")
                return out
            except (ConnectionRefusedError, FileNotFoundError):
                self.logger.warning(
                    "MPV socket not available during batch snapshot, restarting mpv…",
                    extra={
                        "operation": "MPVCommandBatch",
                        "attempt": attempt + 1,
                    },
                )
                try:
                    recovered = self._try_recover_socket_without_restart()
                    if not recovered:
                        if not self._restart_systemd_service_if_needed() or not self._wait_for_socket():
                            _retry_sleep_after_failure(
                                ConnectionRefusedError("socket"), attempt
                            )
                            continue
                    elif not self._check_mpv_socket(timeout=1.0):
                        _retry_sleep_after_failure(ConnectionRefusedError("socket"), attempt)
                        continue
                    self._reset_ipc_session()
                except Exception:
                    _retry_sleep_after_failure(ConnectionRefusedError("socket"), attempt)
                    continue
                continue
            except Exception as e:
                self._reset_ipc_session()
                if attempt == PlaybackConstants.MAX_RETRIES - 1:
                    self.logger.warning(
                        "MPV get_properties_snapshot failed",
                        extra={
                            "operation": "MPVCommandBatch",
                            "attempt": attempt + 1,
                            "error": str(e),
                            "type": type(e).__name__,
                        },
                    )
                else:
                    self.logger.debug(
                        f"Batched MPV snapshot attempt {attempt + 1} failed",
                        extra={
                            "operation": "MPVCommandBatch",
                            "error": str(e),
                            "type": type(e).__name__,
                        },
                    )
                self._note_playback_ipc_failure(e)
                if self._ipc_failure_should_systemd_restart(e):
                    _maybe_restart_mpv_batch(
                        reason=str(e),
                        attempt_num=attempt + 1,
                        ipc_request_id=first_rid,
                    )
                _retry_sleep_after_failure(e, attempt)

        empty: Dict[str, Optional[Any]] = {p: None for p in ordered}
        return empty

    def _send_command(self, command: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        """Отправка команды в MPV. Успехи — только DEBUG (слайдшоу иначе забивает journal)."""
        command_arr = command.get("command", ["unknown"])
        command_name = command_arr[0] if isinstance(command_arr, list) and command_arr else "unknown"
        # For get_property/set_property, include the property name in logs to make "property unavailable"
        # actionable (and to distinguish normal mpv behavior from real errors).
        prop_name: Optional[str] = None
        if isinstance(command_arr, list) and len(command_arr) >= 2 and command_name in ("get_property", "set_property"):
            try:
                prop_name = str(command_arr[1])
            except Exception:
                prop_name = None
        start_time = time.time()

        # IPC command logs can be extremely chatty (polling loops call this frequently).
        # Keep per-command start/end logs OFF by default; allow opt-in via env.
        log_ipc_debug = (os.getenv("DSIGN_MPV_IPC_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"))
        slow_ms = int(os.getenv("DSIGN_MPV_IPC_SLOW_MS", "250") or 250)
        if slow_ms < 0:
            slow_ms = 0

        delays_transport = getattr(
            PlaybackConstants,
            "RETRY_DELAY_TRANSPORT_SEC",
            (0.15, 0.35, 0.75),
        )
        if not isinstance(delays_transport, (list, tuple)):
            delays_transport = (0.15, 0.35, 0.75)

        def _retry_sleep_after_failure(exc: BaseException, attempt_idx: int) -> None:
            if attempt_idx >= PlaybackConstants.MAX_RETRIES - 1:
                return
            if _ipc_error_should_restart_mpv(exc):
                i = min(attempt_idx, len(delays_transport) - 1)
                try:
                    d = float(delays_transport[i])
                except (TypeError, ValueError, IndexError):
                    d = 0.25
                time.sleep(max(0.0, d))
            else:
                time.sleep(PlaybackConstants.RETRY_DELAY)

        def _maybe_restart_mpv_for_transport(*, reason: str, attempt_num: int) -> None:
            """
            One bounded systemd restart per burst of IPC failures (transport drop or hung/no-reply).
            """
            window = float(os.getenv("DSIGN_MPV_RESTART_COALESCE_SEC", "8") or 8)
            if window < 0:
                window = 0.0
            now = time.time()
            with self._mpv_restart_coalesce_lock:
                if window > 0 and (now - self._last_mpv_restart_attempt_ts) < window:
                    return
                self._last_mpv_restart_attempt_ts = now
            self.logger.warning(
                "MPV IPC failure; attempting systemd restart",
                extra={
                    "operation": "MPVCommand",
                    "command": command_name,
                    "attempt": attempt_num,
                    "request_id": ipc_request_id,
                    "reason": reason,
                },
            )
            ok = self._restart_systemd_service_if_needed() and self._wait_for_socket(timeout=12.0)
            if ok:
                self._reset_ipc_session()
            if not ok:
                self.logger.error(
                    "MPV systemd restart after IPC failure did not restore socket",
                    extra={
                        "operation": "MPVCommand",
                        "command": command_name,
                        "attempt": attempt_num,
                        "request_id": ipc_request_id,
                    },
                )

        ipc_request_id = 0
        for attempt in range(PlaybackConstants.MAX_RETRIES):
            ipc_request_id = int(time.time() * 1_000_000) & 0x7FFFFFFF
            if log_ipc_debug:
                self.logger.debug(
                    "MPVCommand started",
                    extra={
                        "command": command_name,
                        **({"property": prop_name} if prop_name else {}),
                        "request_id": ipc_request_id,
                        "timeout": timeout,
                    },
                )
            try:
                if not self._acquire_ipc_lock():
                    self.logger.debug(
                        "IPC lock busy; command skipped",
                        extra={"operation": "MPVCommand", "command": command_name},
                    )
                    return None
                try:
                    sess = self._get_ipc_session()
                    result = sess.command(
                        dict(command),
                        timeout=timeout,
                        request_id=ipc_request_id,
                    )
                finally:
                    self._release_ipc_lock()

                duration_sec = round(time.time() - start_time, 3)
                self._reset_playback_ipc_fail_streak()
                err = result.get("error")
                if err == "success":
                    duration_ms = int(duration_sec * 1000)
                    if log_ipc_debug or (slow_ms and duration_ms >= slow_ms):
                        self.logger.debug(
                            "MPVCommand completed" if log_ipc_debug else "MPVCommand slow",
                            extra={
                                "command": command_name,
                                **({"property": prop_name} if prop_name else {}),
                                "request_id": ipc_request_id,
                                "duration_sec": duration_sec,
                                "attempt": attempt + 1,
                                **({"slow_ms_threshold": slow_ms} if not log_ipc_debug else {}),
                            },
                        )
                else:
                    if command_name == "get_property" and err in (
                        "property unavailable",
                        "property not found",
                    ):
                        if log_ipc_debug:
                            self.logger.debug(
                                "MPVCommand property unavailable",
                                extra={
                                    "command": command_name,
                                    **({"property": prop_name} if prop_name else {}),
                                    "request_id": ipc_request_id,
                                    "duration_sec": duration_sec,
                                    "attempt": attempt + 1,
                                    "mpv_error": err,
                                },
                            )
                        return {
                            "request_id": result.get("request_id", ipc_request_id),
                            "error": "success",
                            "data": None,
                        }
                    quiet_errs = {
                        "property unavailable",
                        "property not found",
                        "error accessing property",
                    }
                    quiet_props = False
                    try:
                        if (
                            command_name == "set_property"
                            and isinstance(prop_name, str)
                            and prop_name.startswith("file-local-options/")
                        ):
                            quiet_props = True
                    except Exception:
                        quiet_props = False
                    log_fn = (
                        self.logger.debug
                        if err in quiet_errs or quiet_props
                        else self.logger.warning
                    )
                    log_fn(
                        "MPVCommand error response",
                        extra={
                            "command": command_name,
                            **({"property": prop_name} if prop_name else {}),
                            "request_id": ipc_request_id,
                            "duration_sec": duration_sec,
                            "attempt": attempt + 1,
                            "mpv_error": err,
                            **({} if err != "property unavailable" else {"response": result}),
                        },
                    )
                return result

            except (ConnectionRefusedError, FileNotFoundError):
                self.logger.warning(
                    "MPV socket not available, attempting recovery...",
                    extra={
                        "operation": "MPVCommand",
                        "command": command_name,
                        "attempt": attempt + 1,
                        "request_id": ipc_request_id,
                        "playback_session": self._playback_session_active,
                    },
                )
                # Lock released: restart + wait without blocking other threads on the lock for minutes.
                try:
                    recovered = self._try_recover_socket_without_restart()
                    if not recovered:
                        if not self._restart_systemd_service_if_needed() or not self._wait_for_socket():
                            _retry_sleep_after_failure(ConnectionRefusedError("socket"), attempt)
                            continue
                    elif not self._check_mpv_socket(timeout=1.0):
                        _retry_sleep_after_failure(ConnectionRefusedError("socket"), attempt)
                        continue
                    self._reset_ipc_session()
                except Exception:
                    _retry_sleep_after_failure(ConnectionRefusedError("socket"), attempt)
                    continue
                continue

            except Exception as e:
                log_level = (
                    self.logger.warning
                    if attempt == PlaybackConstants.MAX_RETRIES - 1
                    else self.logger.debug
                )
                log_level(
                    f"Attempt {attempt + 1} failed",
                    extra={
                        "operation": "MPVCommand",
                        "command": command_name,
                        "attempt": attempt + 1,
                        "request_id": ipc_request_id,
                        "error": str(e),
                        "type": type(e).__name__,
                        "duration_sec": round(time.time() - start_time, 3),
                    },
                )
                self._reset_ipc_session()
                self._note_playback_ipc_failure(e)
                if self._ipc_failure_should_systemd_restart(e):
                    _maybe_restart_mpv_for_transport(
                        reason=str(e), attempt_num=attempt + 1
                    )
                _retry_sleep_after_failure(e, attempt)
                continue

        self.logger.error(
            "Command failed after max attempts",
            extra={
                "operation": "MPVCommand",
                "command": command_name,
                "request_id": ipc_request_id,
                "max_attempts": PlaybackConstants.MAX_RETRIES,
                "duration_sec": round(time.time() - start_time, 3),
            },
        )
        return None

    def initialize(self) -> bool:
        """Инициализация MPV с повторами"""
        try:
            start_time = time.time()
            
            if not self._check_mpv_socket():
                if not self._try_recover_socket_without_restart():
                    if not self._restart_systemd_service_if_needed() or not self._wait_for_socket():
                        raise ConnectionError("MPV socket not available")

            resp = None
            for ping_try in range(8):
                resp = self._send_command({"command": ["get_property", "mpv-version"]})
                if resp and resp.get("error") == "success":
                    break
                time.sleep(0.25)
            if not resp or resp.get("error") != "success":
                raise RuntimeError("MPV not responding properly")
            
            self._mpv_ready = True
            self.logger.info(
                "MPV initialized successfully",
                extra={
                    "operation": "mpv_init",
                    "status": "success",
                    "backend": "DRM",
                    "duration_sec": round(time.time() - start_time, 3)
                }
            )
            return True
            
        except Exception as e:
            self.logger.error(
                "MPV initialization failed",
                extra={
                    "operation": "mpv_init",
                    "status": "failed",
                    "error": str(e),
                    "type": type(e).__name__,
                    "duration_sec": round(time.time() - start_time, 3)
                }
            )
            return False

    def shutdown(self):
        """Корректное завершение работы"""
        self._log_operation("MPVShutdown", "started")
        try:
            start_time = time.time()
            self._send_command({"command": ["quit"]})
            self._log_operation(
                "MPVShutdown",
                "completed",
                {"duration_sec": round(time.time() - start_time, 3)}
            )
        except Exception as e:
            self.logger.error(
                "MPV shutdown error",
                extra={
                    "operation": "MPVShutdown",
                    "error": str(e),
                    "type": type(e).__name__
                }
            )
        finally:
            self._mpv_ready = False
            if self._ipc_session is not None:
                try:
                    self._ipc_session.close()
                except Exception:
                    pass
                self._ipc_session = None

    def update_settings(self, settings: Dict[str, Any]) -> bool:
        """Обновление настроек"""
        self._log_operation(
            "UpdateSettings",
            "started",
            {"settings": settings}
        )
        
        start_time = time.time()
        success = True
        results = {}
        
        for key, value in settings.items():
            if key == "mute":
                if isinstance(value, bool):
                    value = "yes" if value else "no"
                elif isinstance(value, str):
                    lv = value.strip().lower()
                    if lv in ("1", "true", "yes", "on"):
                        value = "yes"
                    elif lv in ("0", "false", "no", "off"):
                        value = "no"
            response = self._send_command({
                "command": ["set_property", key, value]
            })
            result = response.get("error") == "success" if response else False
            results[key] = result
            if not result:
                success = False
        
        self._log_operation(
            "UpdateSettings",
            "completed",
            {
                "success": success,
                "results": results,
                "duration_sec": round(time.time() - start_time, 3)
            }
        )
        return success

    def verify_settings_support(self) -> Dict[str, bool]:
        """Проверка поддерживаемых настроек"""
        self._log_operation("VerifySettings", "started")
        
        test_settings = {
            "volume": 50,
            "speed": 1.0,
            "loop-playlist": "inf"
        }
        results = {}
        
        for key, value in test_settings.items():
            response = self._send_command({
                "command": ["set_property", key, value]
            })
            results[key] = response.get("error") == "success" if response else False
        
        self._log_operation(
            "VerifySettings",
            "completed",
            {"results": results}
        )
        return results
        
    def wait_for_mpv_ready(self, timeout: float = 30, check_interval: float = 1) -> bool:
        """Ожидание готовности MPV"""
        self._log_operation(
            "WaitForMPVReady",
            "started",
            {"timeout": timeout, "check_interval": check_interval}
        )
        
        start_time = time.time()
        last_check = start_time
        
        while time.time() - start_time < timeout:
            if time.time() - last_check >= check_interval:
                self._log_operation(
                    "WaitForMPVReady",
                    "checking",
                    {"elapsed_sec": round(time.time() - start_time, 1)}
                )
                last_check = time.time()
                
                if self._send_command({"command": ["get_property", "idle-active"]}):
                    self._mpv_ready = True
                    self._log_operation(
                        "WaitForMPVReady",
                        "completed",
                        {
                            "success": True,
                            "elapsed_sec": round(time.time() - start_time, 3)
                        }
                    )
                    return True
            
            time.sleep(0.1)
        
        self._log_operation(
            "WaitForMPVReady",
            "timeout",
            {"elapsed_sec": round(time.time() - start_time, 3)}
        )
        return False

    def wait_for_ipc_socket_at_startup(self) -> bool:
        """
        Block until the MPV JSON IPC socket exists (boot / after systemd restart).

        digital-signage may start before dsign-mpv creates the socket; without this wait
        PlaybackService init fails and Flask never binds :5000.
        """
        try:
            attempts = int((os.getenv("DSIGN_MPV_SOCKET_WAIT_ATTEMPTS") or "20").strip())
        except ValueError:
            attempts = 20
        try:
            interval = float((os.getenv("DSIGN_MPV_SOCKET_WAIT_INTERVAL_SEC") or "1").strip())
        except ValueError:
            interval = 1.0
        try:
            restart_after = int((os.getenv("DSIGN_MPV_SOCKET_WAIT_RESTART_AFTER") or "8").strip())
        except ValueError:
            restart_after = 8
        attempts = max(5, min(120, attempts))
        interval = max(0.2, min(5.0, interval))
        restart_after = max(2, min(attempts - 1, restart_after))

        allow_restart = os.getenv("DSIGN_MPV_STARTUP_RESTART_MPV", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        did_restart = False
        for i in range(attempts):
            if self._check_mpv_socket(timeout=0.5):
                return True
            if allow_restart and i >= restart_after and not did_restart:
                did_restart = True
                self.logger.warning(
                    "MPV IPC socket missing during startup; restarting dsign-mpv",
                    extra={"operation": "StartupSocketWait", "attempt": i + 1},
                )
                if self._restart_systemd_service():
                    self._wait_for_socket(timeout=20.0)
            time.sleep(interval)
        return self._check_mpv_socket(timeout=1.0)

    def check_health(self) -> Dict[str, bool]:
        """Комплексная проверка состояния MPV"""
        socket_ok = self._check_mpv_socket()
        systemd_ok = self._check_systemd_service()
        # digital-signage.service runs as user `dsign` — `systemctl is-active` often fails (dbus/policy)
        # while mpv is running and the IPC socket exists. Do not treat that as unhealthy.
        service_ok = systemd_ok or socket_ok
        responsive = False
        if socket_ok:
            responsive = self._send_command({"command": ["get_property", "mpv-version"]}) is not None
        return {
            "service_active": service_ok,
            "socket_available": socket_ok,
            "responsive": responsive,
        }
