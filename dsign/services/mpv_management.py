import os
import json
import socket
import time
from threading import Lock
from typing import Dict, Optional

from .playback_constants import PlaybackConstants

class MPVManager:
    def __init__(self, logger, socketio, upload_folder, mpv_socket=None):
        self.logger = logger
        self.socketio = socketio
        self.mpv_socket = mpv_socket if mpv_socket is not None else PlaybackConstants.SOCKET_PATH
        self.upload_folder = upload_folder
        self._ipc_lock = Lock()
        self._current_settings = {}
        self._mpv_ready = False

        # Создаем директорию для сокета, если не существует
        os.makedirs(os.path.dirname(self.mpv_socket), exist_ok=True)
        
        self.logger.info(f"MPVManager initialized with socket: {self.mpv_socket}")
        
    def _wait_for_socket(self, timeout=10.0):
        """Ожидаем появления сокета с таймаутом"""
        self.logger.info(f"Waiting for MPV socket at {PlaybackConstants.SOCKET_PATH}")
        start_time = time.time()
        while time.time() - start_time < timeout:
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                self.logger.info("Socket found, testing connection...")
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as test_socket:
                        test_socket.settimeout(1.0)
                        test_socket.connect(PlaybackConstants.SOCKET_PATH)
                        self.logger.info("Socket connection successful")
                        return True
                except (ConnectionRefusedError, socket.timeout) as e:
                    self.logger.warning(f"Socket test failed: {str(e)}")
                    time.sleep(0.1)
                    continue
                except Exception as e:
                    self.logger.error(f"Socket test error: {str(e)}")
                    return False
            time.sleep(0.1)
        self.logger.error("Socket wait timeout expired")
        return False

    def _send_command(self, command: Dict, timeout: float = 10.0) -> Optional[Dict]:
        """Отправка команд в MPV с увеличенным таймаутом"""
        for attempt in range(3):
            try:
                with self._ipc_lock, \
                     socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    
                    s.settimeout(timeout)
                    try:
                        s.connect(self.mpv_socket)
                    except ConnectionRefusedError:
                        self.logger.warning("MPV socket connection refused")
                        time.sleep(1)
                        continue
                    
                    cmd_str = json.dumps(command) + '\n'
                    s.sendall(cmd_str.encode())
                    
                    response = b''
                    start_time = time.time()
                    while time.time() - start_time < timeout:
                        try:
                            chunk = s.recv(4096)
                            if not chunk:
                                break
                            response += chunk
                            try:
                                data = json.loads(response.decode())
                                if isinstance(data, dict):
                                    return data
                            except json.JSONDecodeError:
                                continue
                        except socket.timeout:
                            break
                    
                    if not response:
                        raise TimeoutError("No response from MPV")
                    
            except Exception as e:
                self.logger.warning(f"Attempt {attempt+1} failed: {str(e)}")
                if attempt < 2:
                    time.sleep(1)
                continue
        
        self.logger.error(f"Command failed after 3 attempts: {command}")
        return None

    def initialize(self) -> bool:
        """Инициализация с улучшенной проверкой состояния"""
        try:
            if not self._wait_for_socket():
                self.logger.error("MPV socket not found or not responsive")
                return False
                
            response = self._send_command({"command": ["get_property", "mpv-version"]})
            if response and response.get("error") == "success":
                self._mpv_ready = True
                self.logger.info("Successfully connected to MPV service")
                return True
            return False
        except Exception as e:
            self.logger.error(f"MPV initialization failed: {str(e)}")
            return False

    # Остальные методы остаются без изменений
    def update_settings(self, settings: Dict) -> bool:
        """Обновляем настройки MPV"""
        success = True
        for key, value in settings.items():
            response = self._send_command({
                "command": ["set_property", key, value]
            })
            if not response or response.get("error") != "success":
                success = False
                self.logger.warning(f"Failed to set property {key} to {value}")
        return success

    def verify_settings_support(self) -> Dict:
        """Проверяем поддерживаемые настройки через set_property"""
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
        return results
        
    def wait_for_mpv_ready(self, timeout=30, check_interval=1):
        """Явное ожидание готовности MPV"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._send_command({"command": ["get_property", "idle-active"]}):
                self._mpv_ready = True
                return True
            time.sleep(check_interval)
        return False
