import os
import json
import socket
import time
import subprocess
from threading import Lock
from typing import Dict, Optional, Any
from pathlib import Path

from .playback_constants import PlaybackConstants
from .logger import ServiceLogger
from dsign.extensions import socketio

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
        self._current_settings = {}
        self._mpv_ready = False
        self._managed_by_systemd = True

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
        """Отправка команды с трейсингом"""
        command_name = command.get("command", ["unknown"])[0]
        request_id = f"cmd_{time.time():.0f}"
        start_time = time.time()
        
        self._log_operation(
            "MPVCommand",
            "started",
            {
                "command": command_name,
                "request_id": request_id,
                "timeout": timeout
            }
        )
        
        for attempt in range(PlaybackConstants.MAX_RETRIES):
            try:
                with self._ipc_lock, \
                     socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    
                    s.settimeout(timeout)
                    
                    try:
                        s.connect(self.mpv_socket)
                    except (ConnectionRefusedError, FileNotFoundError):
                        self.logger.warning(
                            "MPV socket not available, restarting service...",
                            extra={
                                "operation": "MPVCommand",
                                "command": command_name,
                                "attempt": attempt + 1,
                                "request_id": request_id
                            }
                        )
                        if not self._restart_systemd_service() or not self._wait_for_socket():
                            time.sleep(PlaybackConstants.RETRY_DELAY)
                            continue
                        s.connect(self.mpv_socket)
                    
                    cmd_str = json.dumps(command) + '\n'
                    s.sendall(cmd_str.encode())
                    
                    response = s.recv(4096)
                    if response:
                        result = json.loads(response.decode())
                        self._log_operation(
                            "MPVCommand",
                            "completed",
                            {
                                "command": command_name,
                                "request_id": request_id,
                                "response": result,
                                "duration_sec": round(time.time() - start_time, 3),
                                "attempt": attempt + 1
                            }
                        )
                        return result
                    
                    raise TimeoutError("No response from MPV")
                    
            except Exception as e:
                self.logger.warning(
                    f"Attempt {attempt+1} failed",
                    extra={
                        "operation": "MPVCommand",
                        "command": command_name,
                        "attempt": attempt + 1,
                        "request_id": request_id,
                        "error": str(e),
                        "type": type(e).__name__,
                        "duration_sec": round(time.time() - start_time, 3)
                    }
                )
                if attempt < PlaybackConstants.MAX_RETRIES - 1:
                    time.sleep(PlaybackConstants.RETRY_DELAY)
                continue
        
        self.logger.error(
            "Command failed after max attempts",
            extra={
                "operation": "MPVCommand",
                "command": command_name,
                "request_id": request_id,
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
