import os
import json
import socket
import time
import subprocess
from threading import Lock
from typing import Dict, Optional, Any, Tuple
from pathlib import Path

from .playback_constants import PlaybackConstants
from .logger import ServiceLogger


def _take_one_mpv_ipc_reply_from_buffer(
    buf: bytes, expect_request_id: int
) -> Tuple[Optional[Dict[str, Any]], bytes]:
    """
    Split `buf` on newlines; return (reply_for_expect_request_id, remainder_bytes).
    Incomplete trailing line stays in remainder.
    """
    if not buf:
        return None, buf
    lines = buf.split(b"\n")
    remainder = lines[-1]
    for raw in lines[:-1]:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "error" not in obj:
            continue
        if obj.get("request_id") == expect_request_id:
            return obj, remainder
    return None, buf


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
        # One long-lived Unix socket for all IPC. Opening a new socket per command makes mpv
        # spawn a client thread each time; overnight polling can exhaust resources and stall IPC.
        self._ipc_sock: Optional[socket.socket] = None
        # Leftover bytes after the last parsed IPC line (persistent socket can recv multiple lines).
        self._ipc_recv_buf: bytes = b""
        self._current_settings = {}
        self._mpv_ready = False
        self._managed_by_systemd = True

        # IPC watchdog: if MPV stops answering JSON-IPC (timeouts / empty buffer),
        # the playback thread can get stuck polling. After repeated timeouts we
        # restart MPV service to recover.
        self._ipc_timeout_streak = 0
        self._last_ipc_restart_monotonic = 0.0

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
            resp = self._mpv_manager._send_command({
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
        """Перезапуск systemd сервиса"""
        try:
            start_time = time.time()
            result = subprocess.run(
                ["systemctl", "restart", "dsign-mpv.service"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10.0
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
            self.logger.error(
                "Systemd restart failed",
                extra={
                    "operation": "SystemdServiceRestart",
                    "error": str(e),
                    "stderr": e.stderr.strip()
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

    def _drop_ipc_socket(self) -> None:
        """Close persistent IPC socket (call with _ipc_lock held)."""
        if self._ipc_sock is not None:
            try:
                self._ipc_sock.close()
            except Exception:
                pass
            self._ipc_sock = None
            self._ipc_recv_buf = b""

    def _ensure_ipc_connected(self, *, connect_timeout: float = 5.0) -> bool:
        """Ensure self._ipc_sock is connected (call with _ipc_lock held)."""
        if self._ipc_sock is not None:
            return True
        if not os.path.exists(self.mpv_socket):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(connect_timeout)
        try:
            s.connect(self.mpv_socket)
            self._ipc_sock = s
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            try:
                s.close()
            except Exception:
                pass
            return False

    def _best_effort_ipc_command(self, command_arr: list[Any], *, timeout: float = 2.0) -> bool:
        """
        Best-effort IPC call that does NOT raise.

        Used after watchdog MPV restarts to avoid leaving the screen blank when Flask
        doesn't immediately trigger an idle-logo transition.
        """
        with self._ipc_lock:
            s = self._ipc_sock
            if s is None:
                return False

            ipc_request_id = int(time.time() * 1_000_000) & 0x7FFFFFFF
            payload = {"command": command_arr, "request_id": ipc_request_id}
            cmd_str = json.dumps(payload, ensure_ascii=False) + "\n"
            try:
                s.sendall(cmd_str.encode())
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._drop_ipc_socket()
                return False

            deadline = time.time() + float(timeout)
            while time.time() < deadline:
                result, self._ipc_recv_buf = _take_one_mpv_ipc_reply_from_buffer(
                    self._ipc_recv_buf, ipc_request_id
                )
                if result is not None:
                    return result.get("error") == "success"

                s.settimeout(max(0.05, deadline - time.time()))
                try:
                    chunk = s.recv(16384)
                except socket.timeout:
                    continue
                except (BrokenPipeError, ConnectionResetError, OSError):
                    self._drop_ipc_socket()
                    return False
                if chunk == b"":
                    self._drop_ipc_socket()
                    return False

            return False

    def _best_effort_restore_idle_logo(self) -> None:
        """
        Ensure `idle_logo.jpg` is loaded into MPV after an MPV restart.

        This is intentionally best-effort: it should never break playback.
        """
        try:
            logo_path = Path(self.upload_folder) / "idle_logo.jpg"
            if not logo_path.exists() or not os.access(logo_path, os.R_OK):
                return

            # Keep this minimal and deterministic.
            cmds = [
                ["loadfile", str(logo_path), "replace"],
                ["set_property", "loop-file", "inf"],
                ["set_property", "pause", "no"],
                ["set_property", "panscan", 0.0],
            ]
            for cmd in cmds:
                self._best_effort_ipc_command(cmd, timeout=2.0)
        except Exception:
            # never let diagnostics break playback
            return

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
        
        if not self._check_systemd_service():
            self.logger.warning(
                "MPV service inactive, attempting restart",
                extra={"operation": "SocketWait"}
            )
            if not self._restart_systemd_service():
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
        ipc_request_id = int(time.time() * 1_000_000) & 0x7FFFFFFF
        start_time = time.time()

        # IPC command logs can be extremely chatty (polling loops call this frequently).
        # Keep per-command start/end logs OFF by default; allow opt-in via env.
        log_ipc_debug = (os.getenv("DSIGN_MPV_IPC_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"))
        slow_ms = int(os.getenv("DSIGN_MPV_IPC_SLOW_MS", "250") or 250)
        if slow_ms < 0:
            slow_ms = 0
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

        for attempt in range(PlaybackConstants.MAX_RETRIES):
            try:
                with self._ipc_lock:
                    s = self._ipc_sock
                    if s is None:
                        if os.path.exists(self.mpv_socket):
                            self._ensure_ipc_connected(
                                connect_timeout=min(5.0, float(timeout))
                            )
                        s = self._ipc_sock
                    if s is None:
                        self.logger.warning(
                            "MPV socket not available, restarting service...",
                            extra={
                                "operation": "MPVCommand",
                                "command": command_name,
                                "attempt": attempt + 1,
                                "request_id": ipc_request_id,
                            },
                        )
                        self._drop_ipc_socket()
                        if not self._restart_systemd_service() or not self._wait_for_socket():
                            time.sleep(PlaybackConstants.RETRY_DELAY)
                            continue
                        if not self._ensure_ipc_connected(
                            connect_timeout=min(5.0, float(timeout))
                        ):
                            time.sleep(PlaybackConstants.RETRY_DELAY)
                            continue
                        s = self._ipc_sock

                    assert s is not None

                    payload = dict(command)
                    payload["request_id"] = ipc_request_id
                    cmd_str = json.dumps(payload, ensure_ascii=False) + "\n"
                    try:
                        s.sendall(cmd_str.encode())
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        self._drop_ipc_socket()
                        raise

                    deadline = time.time() + float(timeout)
                    result: Optional[Dict[str, Any]] = None
                    while time.time() < deadline:
                        result, self._ipc_recv_buf = _take_one_mpv_ipc_reply_from_buffer(
                            self._ipc_recv_buf, ipc_request_id
                        )
                        if result is not None:
                            break
                        s.settimeout(max(0.05, deadline - time.time()))
                        try:
                            chunk = s.recv(16384)
                        except socket.timeout:
                            continue
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            self._drop_ipc_socket()
                            raise
                        if chunk == b"":
                            self._drop_ipc_socket()
                            raise ConnectionError("MPV closed IPC connection")
                        self._ipc_recv_buf += chunk

                    if result is not None:
                        duration_sec = round(time.time() - start_time, 3)
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
                            if command_name == "get_property" and err == "property unavailable":
                                if log_ipc_debug:
                                    self.logger.debug(
                                        "MPVCommand property unavailable",
                                        extra={
                                            "command": command_name,
                                            **({"property": prop_name} if prop_name else {}),
                                            "request_id": ipc_request_id,
                                            "duration_sec": duration_sec,
                                            "attempt": attempt + 1,
                                        },
                                    )
                                return {
                                    "request_id": result.get("request_id", ipc_request_id),
                                    "error": "success",
                                    "data": None,
                                }
                            log_fn = self.logger.debug if err == "property unavailable" else self.logger.warning
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

                    self._drop_ipc_socket()
                    raise TimeoutError("No command reply from MPV (events only or empty buffer)")

            except Exception as e:
                is_ipc_timeout = isinstance(e, TimeoutError)
                if is_ipc_timeout:
                    self._ipc_timeout_streak += 1
                else:
                    # Reset streak on non-timeout IPC errors (broken pipe, property unavailable, etc.).
                    self._ipc_timeout_streak = 0

                self.logger.warning(
                    f"Attempt {attempt+1} failed",
                    extra={
                        "operation": "MPVCommand",
                        "command": command_name,
                        "attempt": attempt + 1,
                        "request_id": ipc_request_id,
                        "error": str(e),
                        "type": type(e).__name__,
                        "duration_sec": round(time.time() - start_time, 3)
                    }
                )
                if attempt < PlaybackConstants.MAX_RETRIES - 1:
                    time.sleep(PlaybackConstants.RETRY_DELAY)

                # Restart MPV after a short streak of IPC timeouts, but throttle restarts.
                # This targets the "MPV is alive but never replies to get_property" situation.
                if is_ipc_timeout:
                    streak = int(os.getenv("DSIGN_MPV_IPC_TIMEOUT_RESTART_STREAK", "3") or 3)
                    cooldown = float(os.getenv("DSIGN_MPV_IPC_TIMEOUT_RESTART_COOLDOWN_SEC", "30") or 30)
                    now_mono = time.monotonic()
                    if (
                        self._ipc_timeout_streak >= max(1, streak)
                        and (now_mono - self._last_ipc_restart_monotonic) >= max(5.0, cooldown)
                    ):
                        self._last_ipc_restart_monotonic = now_mono
                        self._ipc_timeout_streak = 0
                        self.logger.error(
                            "MPV IPC watchdog restarting MPV after repeated get_property timeouts",
                            extra={
                                "operation": "MPVCommand",
                                "command": command_name,
                                "timeout_sec": timeout,
                                "streak": streak,
                                "cooldown_sec": cooldown,
                                "request_id": ipc_request_id,
                            },
                        )
                        # Best-effort restart; ignore errors so playback loop can proceed/fallback.
                        try:
                            self._restart_systemd_service()
                            self._wait_for_socket(timeout=20.0)
                            self._ensure_ipc_connected(connect_timeout=min(5.0, float(timeout)))
                            # After an MPV restart, Flask may not re-trigger idle-logo transition
                            # (it only happens at digital-signage start / playlist stop).
                            # Restore idle logo best-effort so the screen doesn't stay blank.
                            self._best_effort_restore_idle_logo()
                        except Exception:
                            pass
                continue
        
        self.logger.error(
            "Command failed after max attempts",
            extra={
                "operation": "MPVCommand",
                "command": command_name,
                "request_id": ipc_request_id,
                "max_attempts": PlaybackConstants.MAX_RETRIES,
                "duration_sec": round(time.time() - start_time, 3)
            }
        )
        return None

    def initialize(self) -> bool:
        """Инициализация MPV с повторами"""
        try:
            start_time = time.time()
            
            if not self._check_mpv_socket():
                if not self._restart_systemd_service() or not self._wait_for_socket():
                    raise ConnectionError("MPV socket not available")
            
            resp = self._send_command({"command": ["get_property", "mpv-version"]})
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
            with self._ipc_lock:
                self._drop_ipc_socket()

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

    def check_health(self) -> Dict[str, bool]:
        """Комплексная проверка состояния MPV"""
        return {
            "service_active": self._check_systemd_service(),
            "socket_available": self._check_mpv_socket(),
            "responsive": self._send_command({"command": ["get_property", "mpv-version"]}) is not None
        }
