import logging
import os
import time
import socket
import subprocess
from pathlib import Path
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

from .mpv_management import MPVManager
from .logo_management import LogoManager
from .profile_management import ProfileManager
from .playlist_management import PlaylistManager
from .playback_constants import PlaybackConstants

class PlaybackService:
    def __init__(self, upload_folder: str, db_session, socketio, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self.socketio = socketio
        self.mpv_socket = PlaybackConstants.SOCKET_PATH
        
        # Initialize components
        self._mpv_manager = MPVManager(
            logger=self.logger,
            socketio=self.socketio,
            mpv_socket=PlaybackConstants.SOCKET_PATH,
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
        
        # Initialize with retry
        self._init_with_retry()

    def _check_mpv_service_active(self) -> bool:
        """Check if dsign-mpv.service is active using systemctl"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "dsign-mpv.service"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                self.logger.warning(f"MPV service not active: {result.stderr.strip()}")
                return False
            return result.stdout.strip() == "active"
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout while checking MPV service status")
            return False
        except Exception as e:
            self.logger.error(f"Failed to check MPV service status: {str(e)}")
            return False

    def _check_ipc_connection(self) -> bool:
        """Check if we can connect to MPV IPC socket"""
        try:
            # 1. Проверяем, что сервис MPV активен
            result = subprocess.run(
                ["systemctl", "is-active", "dsign-mpv.service"],
                capture_output=True,
                text=True
            )
            if result.stdout.strip() != "active":
                self.logger.error("MPV service is not active")
                return False

            # 2. Проверяем существование сокета
            if not os.path.exists(self.mpv_socket):
                self.logger.error(f"MPV socket not found at {self.mpv_socket}")
                return False

            # 3. Проверяем соединение через простую команду
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(5)  # Увеличиваем таймаут
                    s.connect(self.mpv_socket)
                    s.sendall(b'{ "command": ["get_property", "version"] }\n')
                    response = s.recv(1024)
                    if response:  # Любой ответ означает успешное соединение
                        return True
            except Exception as e:
                self.logger.error(f"Socket communication error: {str(e)}")
                return False

            return False
        
        except Exception as e:
            self.logger.error(f"IPC connection check failed: {str(e)}")
            return False

    def _init_with_retry(self, max_attempts: int = 3, initial_delay: float = 2.0):
        """Optimized initialization with parallel checks and backoff"""
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                delay = min(initial_delay * (2 ** attempt), 30)  # Exponential backoff
                
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # Parallel checks
                    checks = {
                        'mpv': executor.submit(self._check_mpv_service_active),
                        'ipc': executor.submit(self._check_ipc_connection),
                        'init': executor.submit(self._mpv_manager.initialize)
                    }
                    
                    # Verify results with timeout
                    for name, future in checks.items():
                        if not future.result(timeout=10):
                            raise RuntimeError(f"{name} check failed")
                    
                    # Background resource loading
                    Thread(target=self._preload_resources).start()
                    
                    return  # Success
                    
            except Exception as e:
                last_exception = e
                self.logger.error(f"Attempt {attempt+1} failed: {str(e)}")
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        self.logger.critical("Initialization failed after all attempts")
        raise RuntimeError(f"Initialization failed: {str(last_exception)}")

    def _preload_resources(self):
        """Non-critical resource loading in background"""
        try:
            self._logo_manager._initialize_default_logo()
            logo_path = self._logo_manager.get_current_logo_path()
            if not logo_path.exists():
                self.logger.warning(f"Logo file missing: {logo_path}")
            
            self._transition_to_idle()  # Non-blocking transition
        except Exception as e:
            self.logger.error(f"Background init error: {str(e)}")

    def _transition_to_idle(self):
        """Transition to idle state with logo"""
        max_attempts = 5
        delay = 2
    
        for attempt in range(max_attempts):
            try:
                if self._logo_manager.display_idle_logo():
                    self.logger.info("Successfully transitioned to idle state")
                    return
                self.logger.warning(f"Idle logo attempt {attempt+1}/{max_attempts} failed")
            except Exception as e:
                self.logger.error(f"Idle transition error: {str(e)}")
        
            time.sleep(delay)
    
        self.logger.critical("Could not establish idle state")
        raise RuntimeError("Could not establish idle state")

    # Delegate methods to appropriate managers
    def play(self, playlist_id: int) -> bool:
        """Play specified playlist"""
        try:
            return self._playlist_manager.play(playlist_id)
        except Exception as e:
            self.logger.error(f"Error playing playlist {playlist_id}: {str(e)}")
            return False

    def stop(self) -> bool:
        """Stop playback and return to idle state"""
        try:
            return self._playlist_manager.stop()
        except Exception as e:
            self.logger.error(f"Error stopping playback: {str(e)}")
            return False

    def get_status(self) -> Dict:
        """Get current playback status"""
        try:
            return self._playlist_manager.get_status()
        except Exception as e:
            self.logger.error(f"Error getting status: {str(e)}")
            return {'error': str(e)}

    def restart_mpv(self) -> bool:
        """Restart MPV process"""
        try:
            return self._playlist_manager.restart_mpv()
        except Exception as e:
            self.logger.error(f"Error restarting MPV: {str(e)}")
            return False

    def get_playback_info(self) -> Dict:
        """Get detailed playback information"""
        try:
            return self._playlist_manager.get_playback_info()
        except Exception as e:
            self.logger.error(f"Error getting playback info: {str(e)}")
            return {'error': str(e)}

    def stop_idle_logo(self):
        """Stop displaying idle logo"""
        try:
            return self._playlist_manager.stop_idle_logo()
        except Exception as e:
            self.logger.error(f"Error stopping idle logo: {str(e)}")
            return False

    def restart_idle_logo(self) -> bool:
        """Restart idle logo display"""
        try:
            return self._playlist_manager.restart_idle_logo()
        except Exception as e:
            self.logger.error(f"Error restarting idle logo: {str(e)}")
            return False

    def display_idle_logo(self, profile_id: int = None) -> bool:
        """Display idle logo"""
        try:
            return self._logo_manager.display_idle_logo(profile_id)
        except Exception as e:
            self.logger.error(f"Error displaying idle logo: {str(e)}")
            return False

    def get_current_logo_path(self) -> Path:
        """Get path to current logo file"""
        try:
            return self._logo_manager.get_current_logo_path()
        except Exception as e:
            self.logger.error(f"Error getting logo path: {str(e)}")
            return Path()

    def get_current_logo_status(self) -> dict:
        """Get status information about current logo"""
        try:
            return self._logo_manager.get_current_logo_status()
        except Exception as e:
            self.logger.error(f"Error getting logo status: {str(e)}")
            return {'error': str(e)}

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get playback profile by ID"""
        try:
            return self._profile_manager.get_profile(profile_id)
        except Exception as e:
            self.logger.error(f"Error getting profile {profile_id}: {str(e)}")
            return None

    def get_all_profiles(self, profile_type: str = None) -> List[Dict]:
        """Get all playback profiles"""
        try:
            return self._profile_manager.get_all_profiles(profile_type)
        except Exception as e:
            self.logger.error(f"Error getting profiles: {str(e)}")
            return []

    def create_profile(self, name: str, profile_type: str, settings: Dict) -> Optional[int]:
        """Create new playback profile"""
        try:
            return self._profile_manager.create_profile(name, profile_type, settings)
        except Exception as e:
            self.logger.error(f"Error creating profile: {str(e)}")
            return None

    def update_profile(self, profile_id: int, name: str, settings: Dict) -> bool:
        """Update existing playback profile"""
        try:
            return self._profile_manager.update_profile(profile_id, name, settings)
        except Exception as e:
            self.logger.error(f"Error updating profile {profile_id}: {str(e)}")
            return False

    def delete_profile(self, profile_id: int) -> bool:
        """Delete playback profile"""
        try:
            return self._profile_manager.delete_profile(profile_id)
        except Exception as e:
            self.logger.error(f"Error deleting profile {profile_id}: {str(e)}")
            return False

    def get_assigned_profile(self, playlist_id: int) -> Optional[Dict]:
        """Get profile assigned to playlist"""
        try:
            return self._profile_manager.get_assigned_profile(playlist_id)
        except Exception as e:
            self.logger.error(f"Error getting assigned profile for playlist {playlist_id}: {str(e)}")
            return None

    def assign_profile_to_playlist(self, playlist_id: int, profile_id: int) -> bool:
        """Assign profile to playlist"""
        try:
            return self._profile_manager.assign_profile_to_playlist(playlist_id, profile_id)
        except Exception as e:
            self.logger.error(f"Error assigning profile to playlist: {str(e)}")
            return False

    def apply_profile(self, profile_id: int) -> bool:
        """Apply profile settings"""
        try:
            return self._profile_manager.apply_profile(profile_id)
        except Exception as e:
            self.logger.error(f"Error applying profile {profile_id}: {str(e)}")
            return False

    def verify_settings_support(self) -> Dict:
        """Verify supported settings"""
        try:
            return self._mpv_manager.verify_settings_support()
        except Exception as e:
            self.logger.error(f"Error verifying settings support: {str(e)}")
            return {'error': str(e)}

    def update_settings(self, settings: Dict) -> bool:
        """Update playback settings"""
        try:
            return self._mpv_manager.update_settings(settings)
        except Exception as e:
            self.logger.error(f"Error updating settings: {str(e)}")
            return False
