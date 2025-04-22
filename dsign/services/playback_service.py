import logging
import os
import time
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, List

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
        
        # Initialize components
        self._mpv_manager = MPVManager(self.logger, self.socketio, self.upload_folder)
        self._logo_manager = LogoManager(self.logger, self.socketio, self.upload_folder, self.db_session, self._mpv_manager)
        self._profile_manager = ProfileManager(self.logger, self.db_session, self._mpv_manager)
        self._playlist_manager = PlaylistManager(
            self.logger, self.socketio, self.upload_folder, 
            self.db_session, self._mpv_manager, self._logo_manager
        )
        
        # Initialize with retry
        self._init_with_retry()

    def _init_with_retry(self, max_attempts: int = 3, delay: float = 2.0):
        """Initialize with retry logic"""
        last_exception = None
    
        for attempt in range(max_attempts):
            try:
                self.logger.info(f"Initializing MPV (attempt {attempt + 1}/{max_attempts})")
            
                # Cleanup any previous failed attempts
                self._cleanup_resources()
            
                # Initialize MPV
                self._mpv_manager._ensure_mpv_service()
                self._mpv_manager._wait_for_mpv_ready()
            
                # Initialize logo
                self._logo_manager._initialize_default_logo()
            
                # Transition to idle
                self._transition_to_idle()
            
                self.logger.info("MPV initialization successful")
                return
            
            except Exception as e:
                last_exception = e
                self.logger.error(f"Init attempt {attempt + 1} failed: {str(e)}", exc_info=True)
                if attempt < max_attempts - 1:
                    time.sleep(delay)
    
        self.logger.critical("MPV initialization failed after all attempts")
        raise RuntimeError(f"Failed to initialize after {max_attempts} attempts: {str(last_exception)}")

    def _cleanup_resources(self):
        """Cleanup resources on failed initialization"""
        try:
            if self._mpv_manager._mpv_process and self._mpv_manager._mpv_process.poll() is None:
                self._mpv_manager._mpv_process.terminate()
        except:
            pass
        
        try:
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                os.unlink(PlaybackConstants.SOCKET_PATH)
        except:
            pass

    def _transition_to_idle(self):
        """Transition to idle state with logo"""
        try:
            for attempt in range(3):
                try:
                    if self._logo_manager.display_idle_logo():
                        return
                    self.logger.warning(f"Failed to display idle logo (attempt {attempt + 1})")
                except Exception as e:
                    self.logger.error(f"Idle transition attempt {attempt + 1} failed: {str(e)}")
                
                time.sleep(1)
            
            self.logger.warning("All idle attempts failed, trying to restart MPV...")
            if self._playlist_manager.restart_mpv():
                if self._logo_manager.display_idle_logo():
                    return
            
            self.logger.error("Could not establish idle state after restart")
            raise RuntimeError("Could not establish idle state")
        
        except Exception as e:
            self.logger.critical(f"Fatal error in idle transition: {str(e)}", exc_info=True)
            raise

    # Delegate methods to appropriate managers
    def play(self, playlist_id: int) -> bool:
        return self._playlist_manager.play(playlist_id)

    def stop(self) -> bool:
        return self._playlist_manager.stop()

    def get_status(self) -> Dict:
        return self._playlist_manager.get_status()

    def restart_mpv(self) -> bool:
        return self._playlist_manager.restart_mpv()

    def get_playback_info(self) -> Dict:
        return self._playlist_manager.get_playback_info()

    def stop_idle_logo(self):
        return self._playlist_manager.stop_idle_logo()

    def restart_idle_logo(self) -> bool:
        return self._playlist_manager.restart_idle_logo()

    def display_idle_logo(self, profile_id: int = None) -> bool:
        return self._logo_manager.display_idle_logo(profile_id)

    def get_current_logo_path(self) -> Path:
        return self._logo_manager.get_current_logo_path()

    def get_current_logo_status(self) -> dict:
        return self._logo_manager.get_current_logo_status()

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        return self._profile_manager.get_profile(profile_id)

    def get_all_profiles(self, profile_type: str = None) -> List[Dict]:
        return self._profile_manager.get_all_profiles(profile_type)

    def create_profile(self, name: str, profile_type: str, settings: Dict) -> Optional[int]:
        return self._profile_manager.create_profile(name, profile_type, settings)

    def update_profile(self, profile_id: int, name: str, settings: Dict) -> bool:
        return self._profile_manager.update_profile(profile_id, name, settings)

    def delete_profile(self, profile_id: int) -> bool:
        return self._profile_manager.delete_profile(profile_id)

    def get_assigned_profile(self, playlist_id: int) -> Optional[Dict]:
        return self._profile_manager.get_assigned_profile(playlist_id)

    def assign_profile_to_playlist(self, playlist_id: int, profile_id: int) -> bool:
        return self._profile_manager.assign_profile_to_playlist(playlist_id, profile_id)

    def apply_profile(self, profile_id: int) -> bool:
        return self._profile_manager.apply_profile(profile_id)

    def verify_settings_support(self) -> Dict:
        return self._mpv_manager.verify_settings_support()

    def update_settings(self, settings: Dict) -> bool:
        return self._mpv_manager.update_settings(settings)
