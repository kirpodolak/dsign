import os
import json
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from threading import Lock, Event
from typing import Dict, Optional, List

from .playback_constants import PlaybackConstants

class MPVManager:
    def __init__(self, logger, socketio, upload_folder):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        self._ipc_lock = Lock()
        self._mpv_ready = Event()
        self._mpv_process = None
        self._monitor_thread = None
        self._stop_monitor = Event()
        self._current_settings = {}
        self._using_drm = False

    def _check_drm_support(self) -> bool:
        """Проверяем поддержку DRM"""
        try:
            result = subprocess.run(
                ['mpv', '--vo=help'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return 'drm' in result.stdout
        except Exception as e:
            self.logger.warning(f"DRM check failed: {str(e)}")
            return False

    def _start_mpv_process(self, use_drm: bool) -> bool:
        """Запускаем процесс MPV"""
        try:
            params = PlaybackConstants.MPV_BASE_PARAMS.copy()
            
            if use_drm:
                params.extend(PlaybackConstants.MPV_DRM_PARAMS)
                self._using_drm = True
                self.logger.info("Starting MPV with DRM output")
            else:
                params.extend(PlaybackConstants.MPV_FALLBACK_PARAMS)
                self._using_drm = False
                self.logger.info("Starting MPV with fallback GPU output")

            # Очищаем старый сокет
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    os.unlink(PlaybackConstants.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Could not remove old socket: {str(e)}")

            self._mpv_process = subprocess.Popen(
                ["mpv"] + params,
                env={**os.environ, **PlaybackConstants.MPV_ENV},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            # Запускаем мониторинг процесса
            self._stop_monitor.clear()
            if self._monitor_thread is None or not self._monitor_thread.is_alive():
                self._monitor_thread = threading.Thread(
                    target=self._monitor_mpv_process,
                    daemon=True
                )
                self._monitor_thread.start()

            return True
        except Exception as e:
            self.logger.error(f"Failed to start MPV: {str(e)}")
            return False

    def _monitor_mpv_process(self):
        """Мониторинг процесса MPV"""
        while not self._stop_monitor.is_set():
            if self._mpv_process.poll() is not None:
                self._mpv_ready.clear()
                self.logger.error("MPV process terminated unexpectedly")
                
                # Пробуем перезапустить
                for attempt in range(3):
                    try:
                        if self._start_mpv_process(self._using_drm):
                            time.sleep(2)  # Даем время на запуск
                            if self._mpv_process.poll() is None:
                                break
                    except Exception as e:
                        self.logger.error(f"Restart attempt {attempt+1} failed: {str(e)}")
                
                if self._mpv_process.poll() is not None:
                    self.logger.error("Failed to restart MPV after 3 attempts")
            
            time.sleep(2)

    def _wait_for_mpv_ready(self, timeout: float = None) -> bool:
        """Ожидаем готовности MPV"""
        timeout = timeout or PlaybackConstants.MPV_START_TIMEOUT
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self._mpv_process.poll() is not None:
                self.logger.error("MPV process terminated while waiting")
                return False
                
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(2)
                        s.connect(PlaybackConstants.SOCKET_PATH)
                        s.sendall(b'{"command": ["get_property", "idle-active"]}\n')
                        response = s.recv(1024)
                        
                        if response and b'"error":"success"' in response:
                            self._mpv_ready.set()
                            self.logger.info("MPV connection established")
                            return True
                except Exception as e:
                    self.logger.debug(f"Socket test failed: {str(e)}")
            
            time.sleep(1)
        
        self.logger.error("Timeout waiting for MPV to become ready")
        return False

    def _send_command(self, command: Dict, timeout: float = None) -> Optional[Dict]:
        """Отправляем команду в MPV"""
        if not isinstance(command, dict) or 'command' not in command:
            self.logger.error(f"Invalid command: {command}")
            return None

        timeout = timeout or PlaybackConstants.SOCKET_TIMEOUT
        
        for attempt in range(PlaybackConstants.MAX_RETRIES):
            try:
                with self._ipc_lock, \
                     socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    
                    s.settimeout(timeout)
                    s.connect(PlaybackConstants.SOCKET_PATH)
                    
                    cmd_str = json.dumps(command) + '\n'
                    s.sendall(cmd_str.encode())
                    
                    response = b''
                    while True:
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
                            
            except Exception as e:
                self.logger.warning(f"Command failed (attempt {attempt+1}): {str(e)}")
                if attempt < PlaybackConstants.MAX_RETRIES - 1:
                    time.sleep(PlaybackConstants.RETRY_DELAY)
        
        self.logger.error(f"Command failed after {PlaybackConstants.MAX_RETRIES} attempts")
        return None

    def initialize(self) -> bool:
        """Инициализация MPV"""
        # Сначала пробуем с DRM
        if self._check_drm_support():
            if not self._start_mpv_process(use_drm=True):
                self.logger.warning("Failed to start with DRM, trying fallback")
                if not self._start_mpv_process(use_drm=False):
                    return False
        else:
            self.logger.warning("DRM not supported, using fallback mode")
            if not self._start_mpv_process(use_drm=False):
                return False
        
        return self._wait_for_mpv_ready()

    def shutdown(self):
        """Корректное завершение работы"""
        self._stop_monitor.set()
        
        if self._mpv_process and self._mpv_process.poll() is None:
            try:
                self._send_command({"command": ["quit"]})
                self._mpv_process.wait(timeout=5)
            except Exception as e:
                self.logger.warning(f"Error during shutdown: {str(e)}")
                try:
                    self._mpv_process.terminate()
                    self._mpv_process.wait(timeout=2)
                except:
                    pass
        
        if os.path.exists(PlaybackConstants.SOCKET_PATH):
            try:
                os.unlink(PlaybackConstants.SOCKET_PATH)
            except:
                pass
