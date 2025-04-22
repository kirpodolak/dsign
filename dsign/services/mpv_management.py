import os
import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, List

from .playback_constants import PlaybackConstants
from .playback_utils import PlaybackUtils

class MPVManager:
    def __init__(self, logger, socketio, upload_folder):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        self._ipc_lock = Lock()
        self._mpv_ready = False
        self._mpv_process = None
        self._socket_ready_event = threading.Event()
        self._last_playback_state = {
            'status': None,
            'playlist_id': None,
            'timestamp': None
        }
        self.supported_properties = []
        self._current_settings = {}

    def _get_supported_mpv_properties(self) -> List[str]:
        """Get list of supported MPV properties"""
        try:
            result = subprocess.run(
                ['mpv', '--list-properties'],
                capture_output=True,
                text=True,
                check=True
            )
            return re.findall(r'^\s*([a-zA-Z-]+)', result.stdout, re.MULTILINE)
        except Exception as e:
            self.logger.error(f"Failed to get MPV properties: {str(e)}")
            return []

    def _filter_supported_settings(self):
        """Filter settings to only include supported properties"""
        for category in list(self._current_settings.keys()):
            self._current_settings[category] = {
                k: v for k, v in self._current_settings[category].items()
                if k in self.supported_properties
            }
            if not self._current_settings[category]:
                del self._current_settings[category]

    def _ensure_mpv_service(self):
        """Ensure MPV service is running with proper environment"""
        try:
            # Ensure socket directory exists with correct permissions
            socket_dir = Path(PlaybackConstants.SOCKET_PATH).parent
            socket_dir.mkdir(mode=0o777, exist_ok=True)
            
            # Clean up old socket if exists
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    os.unlink(PlaybackConstants.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Could not remove old socket: {str(e)}")
                    try:
                        os.chmod(PlaybackConstants.SOCKET_PATH, 0o777)
                        os.unlink(PlaybackConstants.SOCKET_PATH)
                    except Exception as e:
                        self.logger.error(f"Failed to remove socket even after chmod: {str(e)}")

            # Start MPV process with proper environment
            self._mpv_process = subprocess.Popen(
                [
                    "mpv",
                    "--idle",
                    f"--input-ipc-server={PlaybackConstants.SOCKET_PATH}",
                    "--no-config",
                    "--no-osc",
                    "--no-terminal",
                    "--vo=gpu",
                    "--hwdec=auto",
                    "--quiet",
                    "--log-file=/var/log/mpv.log",
                    "--msg-level=all=info"
                ],
                env={**os.environ, **PlaybackConstants.MPV_ENV},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            # Start monitor thread
            threading.Thread(
                target=self._monitor_mpv_process,
                daemon=True
            ).start()

        except Exception as e:
            self.logger.error(f"Failed to start MPV process: {str(e)}", exc_info=True)
            raise RuntimeError(f"MPV service initialization failed: {str(e)}")

    def _monitor_mpv_process(self):
        """Monitor MPV process and restart if needed"""
        while True:
            if self._mpv_process.poll() is not None:
                self.logger.error("MPV process terminated unexpectedly, restarting...")
                self._mpv_ready = False
                try:
                    self._ensure_mpv_service()
                    self._wait_for_mpv_ready()
                    # Try to restore previous state
                    if self._last_playback_state['status'] == 'idle':
                        self.display_idle_logo()
                    elif self._last_playback_state['status'] == 'playing':
                        self.play(self._last_playback_state['playlist_id'])
                except Exception as e:
                    self.logger.error(f"Failed to restart MPV: {str(e)}")
                    time.sleep(10)
            time.sleep(5)

    def _wait_for_mpv_ready(self, timeout: int = None):
        """Wait for MPV IPC socket to be ready with enhanced checks"""
        timeout = timeout or PlaybackConstants.MPV_START_TIMEOUT
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                if not os.path.exists(PlaybackConstants.SOCKET_PATH):
                    time.sleep(1)
                    continue

                # Ensure socket has correct permissions
                if not os.access(PlaybackConstants.SOCKET_PATH, os.R_OK | os.W_OK):
                    try:
                        os.chmod(PlaybackConstants.SOCKET_PATH, 0o666)
                    except Exception as e:
                        self.logger.warning(f"Could not set socket permissions: {str(e)}")
                        time.sleep(1)
                        continue

                # Test connection
                with self._ipc_lock:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(2.0)
                        try:
                            s.connect(PlaybackConstants.SOCKET_PATH)
                            s.sendall(b'{"command": ["get_property", "idle-active"]}\n')
                            response = self._read_socket_response(s)
                            
                            if response and '"error":"success"' in response:
                                self._mpv_ready = True
                                self._socket_ready_event.set()
                                self.logger.info("MPV connection established successfully")
                                return True
                        except ConnectionRefusedError:
                            time.sleep(1)
                            continue
                        except Exception as e:
                            self.logger.debug(f"Connection test failed: {str(e)}")
                            time.sleep(1)
                            continue
                        
            except Exception as e:
                self.logger.debug(f"MPV ready check failed: {str(e)}")
                time.sleep(1)
                
        self.logger.error("Timeout waiting for MPV to become ready")
        if self._mpv_process and self._mpv_process.poll() is None:
            try:
                self._mpv_process.terminate()
                self._mpv_process.wait(timeout=5)
            except:
                pass
        raise RuntimeError("Timeout waiting for MPV IPC socket to become ready")

    def _read_socket_response(self, sock: socket.socket, timeout: float = 1.0) -> Optional[str]:
        """Read complete response from socket with timeout handling"""
        sock.settimeout(timeout)
        response = b''
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                
                # Try to parse as complete JSON or multiple JSONs
                decoded = response.decode()
                try:
                    json.loads(decoded)
                    return decoded
                except json.JSONDecodeError:
                    if '\n' in decoded:
                        parts = [p.strip() for p in decoded.split('\n') if p.strip()]
                        if all(PlaybackUtils.validate_json(p) for p in parts):
                            return decoded
                    continue
                    
            except socket.timeout:
                break
            except Exception as e:
                self.logger.debug(f"Socket read error: {str(e)}")
                break
                
        return response.decode() if response else None

    def _send_command(self, command: Dict) -> Optional[Dict]:
        """Send command to MPV via IPC socket with robust error handling"""
        if not isinstance(command, dict) or 'command' not in command:
            self.logger.error(f"Invalid command structure: {command}")
            return None

        if not self._mpv_ready:
            self.logger.warning("MPV not ready, attempting to reconnect...")
            try:
                self._wait_for_mpv_ready()
            except Exception as e:
                self.logger.error(f"Failed to reconnect to MPV: {str(e)}")
                return None
    
        for attempt in range(PlaybackConstants.MAX_RETRIES):
            with self._ipc_lock:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(PlaybackConstants.SOCKET_TIMEOUT)
                        s.connect(PlaybackConstants.SOCKET_PATH)
                        
                        cmd_str = json.dumps(command) + '\n'
                        s.sendall(cmd_str.encode())
                        
                        response = self._read_socket_response(s)
                        if response:
                            try:
                                responses = [json.loads(r) for r in response.strip().split('\n') if r.strip()]
                                for data in responses:
                                    if isinstance(data, dict):
                                        if data.get('error') == 'success':
                                            self.logger.debug(f"MPV command succeeded: {command}")
                                            return data
                                        elif 'error' in data:
                                            self.logger.error(f"MPV command failed: {data['error']} (command: {command})")
                                            return data
                                        return data
                            except json.JSONDecodeError as e:
                                self.logger.error(f"Failed to decode MPV response: {str(e)}")
                                continue
                                
                except ConnectionError as e:
                    if attempt == PlaybackConstants.MAX_RETRIES - 1:
                        self.logger.error(f"IPC command failed after {PlaybackConstants.MAX_RETRIES} attempts: {str(e)}")
                        self._mpv_ready = False
                    time.sleep(PlaybackConstants.RETRY_DELAY)
                except Exception as e:
                    self.logger.error(f"IPC command failed: {str(e)}")
                    break
                    
        return None
