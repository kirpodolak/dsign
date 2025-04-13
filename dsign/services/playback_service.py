import os
import json
import logging
import subprocess
import socket
import re
import time
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

    def __init__(self, upload_folder: str, db_session, socketio, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self.socketio = socketio
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)
        self._ipc_lock = Lock()
        self._mpv_ready = False
        
        # Initialize MPV properties and settings
        self._initialize_mpv()

    def _initialize_mpv(self):
        """Initialize MPV properties and settings"""
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
        """Ensure MPV service is running"""
        try:
            for attempt in range(self.MAX_RETRIES):
                try:
                    result = subprocess.run(
                        ["sudo", "systemctl", "enable", "--now", "dsign-mpv.service"],
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    self.logger.info("MPV service started successfully")
                    return
                except subprocess.CalledProcessError as e:
                    if attempt == self.MAX_RETRIES - 1:
                        raise
                    time.sleep(self.RETRY_DELAY)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip()
            self.logger.error(f"Failed to start MPV service: {error_msg}")
            raise RuntimeError(f"MPV service initialization failed: {error_msg}")

    def _wait_for_mpv_ready(self, timeout: int = 10):
        """Wait for MPV IPC socket to be ready"""
        start_time = time.time()
        last_error_time = 0
        error_interval = 2  # seconds between error logs
        
        while time.time() - start_time < timeout:
            if os.path.exists(self.SOCKET_PATH):
                try:
                    if not os.access(self.SOCKET_PATH, os.R_OK | os.W_OK):
                        current_time = time.time()
                        if current_time - last_error_time >= error_interval:
                            self.logger.error(f"Insufficient permissions for socket {self.SOCKET_PATH}")
                            last_error_time = current_time
                        time.sleep(0.5)
                        continue
                    
                    with self._ipc_lock:
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                            s.settimeout(1.0)
                            s.connect(self.SOCKET_PATH)
                            s.sendall(b'{"command": ["get_property", "idle-active"]}\n')
                            response = s.recv(1024)
                            if response:
                                try:
                                    data = json.loads(response.decode())
                                    if isinstance(data, dict) and 'data' in data:
                                        self._mpv_ready = True
                                        return True
                                except json.JSONDecodeError:
                                    continue
                except Exception as e:
                    current_time = time.time()
                    if current_time - last_error_time >= error_interval:
                        self.logger.warning(f"MPV socket connection attempt failed: {str(e)}")
                        last_error_time = current_time
                    time.sleep(0.5)
                    continue
            time.sleep(0.1)
        
        raise RuntimeError("MPV IPC socket not ready or inaccessible")

    def _send_command(self, command: Dict) -> Optional[Dict]:
        """Send command to MPV via IPC socket with retries"""
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
                        
                        response = b''
                        while True:
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
                        
                        if response:
                            try:
                                data = json.loads(response.decode())
                                if isinstance(data, dict):
                                    return data
                            except json.JSONDecodeError as e:
                                self.logger.error(f"Failed to decode MPV response: {e}")
                        return None
                        
                except ConnectionError as e:
                    if attempt == self.MAX_RETRIES - 1:
                        self.logger.error(f"IPC command failed after {self.MAX_RETRIES} attempts: {str(e)}")
                        self._mpv_ready = False
                        return None
                    time.sleep(self.RETRY_DELAY)
                except Exception as e:
                    self.logger.error(f"IPC command failed: {str(e)}")
                    return None
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
                    
                    if response is not None:
                        self._current_settings[category][setting] = value
                        results.append(True)
                    else:
                        results.append(False)
                        self.logger.warning(f"Failed to set {setting} to {value}")
        
        self.socketio.emit('settings_updated', self._current_settings)
        return all(results)

    # Profile management methods
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

    # Playback control methods
    def display_idle_logo(self, profile_id: int = None) -> bool:
        """Display idle logo with optional profile"""
        if profile_id:
            self.apply_profile(profile_id)
            
        logo_path = self.upload_folder / self.DEFAULT_LOGO
        if not logo_path.exists():
            self.logger.warning(f"Idle logo not found at {logo_path}")
            return False

        try:
            load_res = self._send_command({
                "command": ["loadfile", str(logo_path), "replace"],
                "async": True
            })
            if not load_res:
                raise RuntimeError("Failed to load idle logo")
            
            loop_res = self._send_command({
                "command": ["set_property", "loop", "inf"]
            })
            mute_res = self._send_command({
                "command": ["set_property", "mute", "yes"]
            })
            
            if not loop_res or not mute_res:
                self.logger.warning("Partial success while setting idle logo properties")
                
            self._update_playback_status(None, 'idle')
            self.socketio.emit('playback_state', {
                'status': 'idle',
                'settings': self._current_settings
            })
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to display idle logo: {str(e)}")
            return False

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
                "command": ["loadlist", str(playlist_file), "replace"],
                "async": True
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
            subprocess.run(
                ["sudo", "systemctl", "restart", "dsign-mpv.service"],
                check=True
            )
            time.sleep(2)
            self._wait_for_mpv_ready()
            self.display_idle_logo()
            return True
        except Exception as e:
            self.logger.error(f"Failed to restart MPV: {str(e)}")
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
