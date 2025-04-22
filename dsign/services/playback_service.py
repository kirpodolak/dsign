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
        self._logo_manager = LogoManager(self.logger, self.socketio, self.upload_folder, 
                                       self.db_session, self._mpv_manager)
        self._profile_manager = ProfileManager(self.logger, self.db_session, self._mpv_manager)
        self._playlist_manager = PlaylistManager(
            self.logger, self.socketio, self.upload_folder,
            self.db_session, self._mpv_manager, self._logo_manager
        )
        
        # Initialize with retry
        self._init_with_retry()

    def _init_with_retry(self, max_attempts: int = 3, delay: float = 5.0):
        """Initialize with retry logic"""
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                self.logger.info(f"Initializing playback service (attempt {attempt + 1}/{max_attempts})")
                
                # Cleanup any previous failed attempts
                self._cleanup_resources()
                
                # Initialize MPV
                if not self._mpv_manager.initialize():
                    raise RuntimeError("MPV initialization failed")
                
                # Initialize logo
                self._logo_manager._initialize_default_logo()
                
                # Verify logo file exists
                logo_path = self._logo_manager.get_current_logo_path()
                if not logo_path.exists():
                    raise FileNotFoundError(f"Logo file not found: {logo_path}")
                
                # Transition to idle
                self._transition_to_idle()
                
                self.logger.info("Playback service initialized successfully")
                return
                
            except Exception as e:
                last_exception = e
                self.logger.error(f"Initialization attempt {attempt + 1} failed: {str(e)}", exc_info=True)
                
                # Cleanup
                self._cleanup_resources()
                
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        self.logger.critical("Playback service initialization failed after all attempts")
        raise RuntimeError(f"Failed to initialize after {max_attempts} attempts: {str(last_exception)}")

    def _cleanup_resources(self):
        """Cleanup resources on failed initialization"""
        try:
            if hasattr(self._mpv_manager, 'shutdown'):
                self._mpv_manager.shutdown()
            elif hasattr(self._mpv_manager, '_mpv_process'):
                if self._mpv_manager._mpv_process and self._mpv_manager._mpv_process.poll() is None:
                    self._mpv_manager._mpv_process.terminate()
        except Exception as e:
            self.logger.warning(f"Error during MPV cleanup: {str(e)}")
        
        try:
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                os.unlink(PlaybackConstants.SOCKET_PATH)
        except Exception as e:
            self.logger.warning(f"Error removing socket: {str(e)}")

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
            
            raise RuntimeError("Could not establish idle state")
        
        except Exception as e:
            self.logger.critical(f"Fatal error in idle transition: {str(e)}", exc_info=True)
            raise

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
