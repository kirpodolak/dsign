import os
import json
import logging
import subprocess
import socket
import re
import time
import threading
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from threading import Lock
from datetime import datetime

class PlaybackService:
    DEFAULT_LOGO = 'idle_logo.jpg'
    PREVIEW_FILE = 'On_Air_Preview.jpg'
    SOCKET_PATH = '/tmp/mpv-socket'
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
    MAX_RETRIES = 3
    RETRY_DELAY = 0.5
    SOCKET_TIMEOUT = 2.0
    MPV_START_TIMEOUT = 15

    def __init__(self, upload_folder: str, db_session, socketio, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self.socketio = socketio
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)
        self._ipc_lock = Lock()
        self._mpv_ready = False
        self._mpv_process = None
        self._socket_ready_event = threading.Event()
        self._last_playback_state = {'status': None, 'playlist_id': None}
        
        # Initialize MPV properties and settings
        self._initialize_mpv()

    def _initialize_mpv(self):
        """Initialize MPV properties and settings with enhanced reliability"""
        try:
            self.supported_properties = self._get_supported_mpv_properties()
            
            self.setting_validators = {
                'video-zoom': lambda x: 0.1 <= float(x) <= 2.0,
                'video-aspect': lambda x: x in ['16:9', '4:3', '1.85:1', 'auto'],
                'video-rotate': lambda x: x in [0, 90, 180, 270],
                'brightness': lambda x: -100 <= int(x) <= 100,
                'contrast': lambda x: -100 <= int(x) <= 100,
                'saturation': lambda x: -100 <= int(x) <= 100,
                'gamma': lambda x: -100 <= int(x) <= 100,
                'volume': lambda x: 0 <= int(x) <= 100,
                'mute': lambda x: isinstance(x, bool),
                'speed': lambda x: 0.1 <= float(x) <= 10.0,
                'loop-playlist': lambda x: x in ['inf', 'no', 'force'],
                'audio-delay': lambda x: -10.0 <= float(x) <= 10.0
            }

            self._current_settings = {
                'video': {
                    'video-zoom': 1.0,
                    'video-aspect': self.DEFAULT_ASPECT_RATIO,
                    'video-rotate': 0,
                    'brightness': 0,
                    'contrast': 0,
                    'saturation': 0,
                    'gamma': 0
                },
                'audio': {
                    'volume': 100,
                    'mute': False,
                    'audio-delay': 0
                },
                'playback': {
                    'speed': 1.0,
                    'pause': False,
                    'loop-playlist': 'inf'
                }
            }

            self._filter_supported_settings()
            self._ensure_mpv_service()
            self._wait_for_mpv_ready()
            
        except Exception as e:
            self.logger.error(f"MPV initialization failed: {str(e)}", exc_info=True)
            raise RuntimeError(f"MPV initialization failed: {str(e)}")

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
            socket_dir = Path(self.SOCKET_PATH).parent
            socket_dir.mkdir(mode=0o777, exist_ok=True)
            
            # Clean up old socket if exists
            if os.path.exists(self.SOCKET_PATH):
                try:
                    os.unlink(self.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Could not remove old socket: {str(e)}")

            # Start MPV process directly with proper environment
            env = {
                **os.environ,
                "DISPLAY": ":0",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "HOME": "/var/www",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"
            }

            self._mpv_process = subprocess.Popen(
                [
                    "mpv",
                    "--idle",
                    f"--input-ipc-server={self.SOCKET_PATH}",
                    "--no-config",
                    "--no-osc",
                    "--no-terminal",
                    "--vo=gpu",
                    "--hwdec=auto",
                    "--quiet"
                ],
                env=env,
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
                    time.sleep(10)  # Longer delay after failed restart
            time.sleep(5)

    def _wait_for_mpv_ready(self, timeout: int = None):
        """Wait for MPV IPC socket to be ready with enhanced checks"""
        timeout = timeout or self.MPV_START_TIMEOUT
        start_time = time.time()
        last_log_time = 0
        log_interval = 2  # seconds between logs
        
        while time.time() - start_time < timeout:
            try:
                if not os.path.exists(self.SOCKET_PATH):
                    current_time = time.time()
                    if current_time - last_log_time > log_interval:
                        self.logger.info(f"Waiting for MPV socket at {self.SOCKET_PATH}...")
                        last_log_time = current_time
                    time.sleep(0.5)
                    continue

                # Ensure socket has correct permissions
                if not os.access(self.SOCKET_PATH, os.R_OK | os.W_OK):
                    try:
                        os.chmod(self.SOCKET_PATH, 0o666)
                    except Exception as e:
                        self.logger.warning(f"Could not set socket permissions: {str(e)}")

                # Test connection
                with self._ipc_lock:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(1.0)
                        s.connect(self.SOCKET_PATH)
                        s.sendall(b'{"command": ["get_property", "idle-active"]}\n')
                        response = self._read_socket_response(s)
                        
                        if response:
                            try:
                                # Handle multiple JSON responses
                                responses = [json.loads(r) for r in response.strip().split('\n') if r.strip()]
                                for data in responses:
                                    if isinstance(data, dict):
                                        if data.get('error') == 'success':
                                            self.logger.debug("MPV connection test succeeded")
                                            self._mpv_ready = True
                                            self._socket_ready_event.set()
                                            self.logger.info("MPV connection established successfully")
                                            return True
                                        elif 'data' in data:
                                            self._mpv_ready = True
                                            self._socket_ready_event.set()
                                            self.logger.info("MPV connection established successfully")
                                            return True
                            except json.JSONDecodeError as e:
                                self.logger.debug(f"JSON decode error: {str(e)}")
                                continue
                            
            except ConnectionRefusedError:
                time.sleep(0.5)
                continue
            except Exception as e:
                current_time = time.time()
                if current_time - last_log_time > log_interval:
                    self.logger.warning(f"MPV connection attempt failed: {str(e)}")
                    last_log_time = current_time
                time.sleep(0.5)
                
        self.logger.error("Timeout waiting for MPV to become ready")
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
                    # Try single JSON first
                    json.loads(decoded)
                    return decoded
                except json.JSONDecodeError:
                    # Try splitting by newlines if there are multiple JSONs
                    if '\n' in decoded:
                        parts = [p.strip() for p in decoded.split('\n') if p.strip()]
                        if all(self._is_valid_json(p) for p in parts):
                            return decoded
                    continue
                    
            except socket.timeout:
                break
            except Exception as e:
                self.logger.debug(f"Socket read error: {str(e)}")
                break
                
        return response.decode() if response else None

    def _is_valid_json(self, json_str: str) -> bool:
        """Helper method to validate JSON string"""
        try:
            json.loads(json_str)
            return True
        except json.JSONDecodeError:
            return False

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
    
        for attempt in range(self.MAX_RETRIES):
            with self._ipc_lock:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(self.SOCKET_TIMEOUT)
                        s.connect(self.SOCKET_PATH)
                        
                        cmd_str = json.dumps(command) + '\n'
                        s.sendall(cmd_str.encode())
                        
                        response = self._read_socket_response(s)
                        if response:
                            try:
                                # Handle multiple JSON responses
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
                    if attempt == self.MAX_RETRIES - 1:
                        self.logger.error(f"IPC command failed after {self.MAX_RETRIES} attempts: {str(e)}")
                        self._mpv_ready = False
                    time.sleep(self.RETRY_DELAY)
                except Exception as e:
                    self.logger.error(f"IPC command failed: {str(e)}")
                    break
                    
        return None

    def _validate_settings(self, settings: Dict) -> bool:
        """Validate playback settings"""
        for category, values in settings.items():
            for setting, value in values.items():
                if setting in self.setting_validators:
                    if not self.setting_validators[setting](value):
                        self.logger.warning(f"Invalid value for {setting}: {value}")
                        return False
        return True

    def verify_settings_support(self) -> Dict:
        """Return information about supported settings"""
        return {
            'supported_settings': self._current_settings,
            'all_supported_properties': self.supported_properties,
            'validators': {k: str(v) for k, v in self.setting_validators.items()}
        }

    def update_settings(self, settings: Dict) -> bool:
        """Update playback settings"""
        if not self._validate_settings(settings):
            return False
            
        results = []
        
        for category, values in settings.items():
            if category not in self._current_settings:
                continue
                
            for setting, value in values.items():
                if setting in self._current_settings[category]:
                    response = self._send_command({
                        "command": ["set_property", setting, value],
                        "async": True
                    })
                    
                    if response is not None and 'error' not in response:
                        self._current_settings[category][setting] = value
                        results.append(True)
                    else:
                        results.append(False)
                        self.logger.warning(f"Failed to set {setting} to {value}")
        
        self.socketio.emit('settings_updated', self._current_settings)
        return all(results)

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get profile by ID"""
        from ..models import PlaybackProfile
        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if profile:
            return {
                'id': profile.id,
                'name': profile.name,
                'type': profile.profile_type,
                'settings': json.loads(profile.settings),
                'created_at': profile.created_at.isoformat()
            }
        return None

    def get_all_profiles(self, profile_type: str = None) -> List[Dict]:
        """Get all profiles"""
        from ..models import PlaybackProfile
        query = self.db_session.query(PlaybackProfile)
        if profile_type:
            query = query.filter_by(profile_type=profile_type)
        return [{
            'id': p.id,
            'name': p.name,
            'type': p.profile_type,
            'settings': json.loads(p.settings),
            'created_at': p.created_at.isoformat()
        } for p in query.all()]

    def create_profile(self, name: str, profile_type: str, settings: Dict) -> Optional[int]:
        """Create new profile"""
        from ..models import PlaybackProfile
        if not self._validate_settings(settings):
            return None
            
        profile = PlaybackProfile(
            name=name,
            profile_type=profile_type,
            settings=json.dumps(settings),
            created_at=datetime.utcnow()
        )
        self.db_session.add(profile)
        self.db_session.commit()
        return profile.id

    def update_profile(self, profile_id: int, name: str, settings: Dict) -> bool:
        """Update existing profile"""
        from ..models import PlaybackProfile
        if not self._validate_settings(settings):
            return False
            
        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if profile:
            profile.name = name
            profile.settings = json.dumps(settings)
            self.db_session.commit()
            return True
        return False

    def delete_profile(self, profile_id: int) -> bool:
        """Delete profile"""
        from ..models import PlaybackProfile
        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if profile:
            self.db_session.delete(profile)
            self.db_session.commit()
            return True
        return False

    def get_assigned_profile(self, playlist_id: int) -> Optional[Dict]:
        """Get profile assigned to playlist"""
        from ..models import PlaylistProfileAssignment
        assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
            playlist_id=playlist_id
        ).first()
        if assignment:
            return self.get_profile(assignment.profile_id)
        return None

    def assign_profile_to_playlist(self, playlist_id: int, profile_id: int) -> bool:
        """Assign profile to playlist"""
        from ..models import PlaylistProfileAssignment
        assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
            playlist_id=playlist_id
        ).first()
        
        if assignment:
            assignment.profile_id = profile_id
        else:
            assignment = PlaylistProfileAssignment(
                playlist_id=playlist_id,
                profile_id=profile_id
            )
            self.db_session.add(assignment)
        
        self.db_session.commit()
        return True

    def apply_profile(self, profile_id: int) -> bool:
        """Apply profile settings"""
        profile = self.get_profile(profile_id)
        if profile and self._validate_settings(profile['settings']):
            return self.update_settings(profile['settings'])
        return False

    def display_idle_logo(self, profile_id: int = None) -> bool:
        """Display idle logo with multiple fallback strategies"""
        try:
            # Store current state
            self._last_playback_state = {'status': 'idle', 'playlist_id': None}
            
            # Apply profile if specified
            if profile_id:
                if not self.apply_profile(profile_id):
                    self.logger.error(f"Failed to apply profile {profile_id}")
                    return False

            logo_path = self.upload_folder / self.DEFAULT_LOGO
            self.logger.info(f"Displaying logo from: {logo_path}")

            if not logo_path.exists():
                self.logger.error(f"Logo file not found at {logo_path}")
                return False

            # Try primary method
            load_res = self._send_command({
                "command": ["loadfile", str(logo_path), "replace"]
            })
            
            if load_res and 'error' not in load_res:
                # Set additional properties
                loop_res = self._send_command({"command": ["set_property", "loop", "inf"]})
                mute_res = self._send_command({"command": ["set_property", "mute", "yes"]})
                
                if not loop_res or not mute_res:
                    self.logger.warning("Failed to set some logo properties")
                
                self._update_playback_status(None, 'idle')
                self.socketio.emit('playback_state', {
                    'status': 'idle',
                    'settings': self._current_settings
                })
                return True
            else:
                # Fallback to alternative method
                return self._fallback_logo_display(logo_path)

        except Exception as e:
            self.logger.error(f"Logo display failed: {str(e)}", exc_info=True)
            try:
                self.logger.info("Attempting MPV restart...")
                if self.restart_mpv():
                    return self.display_idle_logo(profile_id)
            except Exception as restart_error:
                self.logger.error(f"MPV restart failed: {str(restart_error)}")
            return False

    def _fallback_logo_display(self, logo_path: Path) -> bool:
        """Alternative method for displaying logo when primary fails"""
        try:
            # Try with different parameters
            cmd = {
                "command": [
                    "loadfile", 
                    str(logo_path),
                    "replace"
                ]
            }
            
            res = self._send_command(cmd)
            if res and 'error' not in res:
                loop_res = self._send_command({"command": ["set_property", "loop", "inf"]})
                mute_res = self._send_command({"command": ["set_property", "mute", "yes"]})
                return loop_res is not None and mute_res is not None
                
            # Last resort - restart MPV and try again
            self.logger.warning("Attempting MPV restart as last resort...")
            if self.restart_mpv():
                return self._send_command(cmd) is not None
                
            return False
            
        except Exception as e:
            self.logger.error(f"Fallback logo display failed: {str(e)}")
            return False

    def get_current_logo_path(self) -> Path:
        """Safely get current logo path"""
        try:
            return self._find_logo_file()
        except FileNotFoundError:
            return Path(__file__).parent.parent / "static" / "images" / "placeholder.jpg"

    def _find_logo_file(self) -> Path:
        """Find current logo file"""
        logo_path = self.upload_folder / self.DEFAULT_LOGO
        if logo_path.exists():
            return logo_path
        raise FileNotFoundError(f"No logo file found at {logo_path}")

    def get_current_logo_status(self) -> dict:
        """Get complete logo status for API"""
        try:
            path = self._find_logo_file()
            return {
                "path": str(path),
                "is_default": "placeholder.jpg" in str(path),
                "file_size": os.path.getsize(path),
                "last_modified": os.path.getmtime(path)
            }
        except FileNotFoundError:
            return {
                "path": "",
                "is_default": True,
                "error": "no_logo_found"
            }

    def _create_playlist_file(self, playlist) -> Path:
        """Create temporary playlist file"""
        playlist_file = self.tmp_dir / f'playlist_{playlist.id}.txt'
        with open(playlist_file, 'w') as f:
            for file in playlist.files:
                file_path = self.upload_folder / file.file_name
                if file_path.exists():
                    f.write(f"file '{file_path}'\n")
                else:
                    self.logger.warning(f"File {file_path} not found, skipping")
        return playlist_file

    def play(self, playlist_id: int) -> bool:
        """Play playlist with optional profile"""
        from ..models import Playlist
        
        try:
            # Store current state
            self._last_playback_state = {'status': 'playing', 'playlist_id': playlist_id}
            
            if not os.path.exists(self.SOCKET_PATH):
                raise RuntimeError(f"MPV socket not found at {self.SOCKET_PATH}")
            
            if not os.access(self.SOCKET_PATH, os.R_OK | os.W_OK):
                raise PermissionError(f"No access to MPV socket at {self.SOCKET_PATH}")
                
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            assigned_profile = self.get_assigned_profile(playlist_id)
            if assigned_profile:
                self.apply_profile(assigned_profile['id'])

            playlist_file = self._create_playlist_file(playlist)
            
            load_res = self._send_command({
                "command": ["loadlist", str(playlist_file), "replace"]
            })
            
            loop_res = self._send_command({
                "command": ["set_property", "loop-playlist", "inf"]
            })
            
            if not load_res or not loop_res:
                raise RuntimeError("Failed to set playlist properties")
            
            self._update_playback_status(playlist_id, 'playing')
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist': {'id': playlist.id, 'name': playlist.name},
                'settings': self._current_settings
            })
            return True
            
        except Exception as e:
            self.logger.error(f"Playback error: {str(e)}", exc_info=True)
            self.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(self) -> bool:
        """Stop playback"""
        try:
            return self.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Stop error: {str(e)}", exc_info=True)
            return False

    def _update_playback_status(self, playlist_id: Optional[int], status: str):
        """Update playback status in database"""
        from ..models import PlaybackStatus
        
        playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
        playback.playlist_id = playlist_id
        playback.status = status
        self.db_session.add(playback)
        self.db_session.commit()

    def get_status(self) -> Dict:
        """Get current playback status"""
        from ..models import PlaybackStatus
        
        status = self.db_session.query(PlaybackStatus).first()
        return {
            'status': status.status if status else None,
            'playlist_id': status.playlist_id if status else None,
            'settings': self._current_settings
        }

    def capture_preview(self) -> bool:
        """Capture current playback preview"""
        preview_path = Path(__file__).parent.parent / 'static' / self.PREVIEW_FILE
        
        try:
            if preview_path.exists():
                preview_path.unlink()
                
            res = self._send_command({
                "command": ["screenshot-to-file", str(preview_path), "video"]
            })
            
            time.sleep(0.5)
            return preview_path.exists() and res is not None
            
        except Exception as e:
            self.logger.error(f"Preview capture failed: {str(e)}")
            return False

    def restart_mpv(self) -> bool:
        """Restart MPV process"""
        try:
            # Clean up existing process
            if self._mpv_process:
                try:
                    self._mpv_process.terminate()
                    self._mpv_process.wait(timeout=5)
                except Exception as e:
                    self.logger.warning(f"Error terminating MPV process: {str(e)}")
            
            # Clean up socket
            if os.path.exists(self.SOCKET_PATH):
                try:
                    os.unlink(self.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Error removing socket: {str(e)}")
            
            # Start fresh
            self._mpv_ready = False
            self._ensure_mpv_service()
            self._wait_for_mpv_ready()
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to restart MPV: {str(e)}", exc_info=True)
            return False

    def get_playback_info(self) -> Dict:
        """Get current playback info"""
        info = {}
        for category, settings in self._current_settings.items():
            info[category] = {}
            for setting in settings.keys():
                response = self._send_command({
                    "command": ["get_property", setting]
                })
                if response and 'data' in response:
                    info[category][setting] = response['data']
        return info
        
    def stop_idle_logo(self):
        """Stop idle logo display"""
        try:
            res = self._send_command({"command": ["stop"]})
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
            return self.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Failed to restart idle logo: {str(e)}")
            return False
