import os
import time
from pathlib import Path
from typing import Dict, Optional

from .playback_constants import PlaybackConstants

class LogoManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self._mpv_manager = mpv_manager
        self._last_playback_state = {}

    def _initialize_default_logo(self):
        logo_path = self.upload_folder / PlaybackConstants.DEFAULT_LOGO
        if not logo_path.exists():
            try:
                default_logo = Path(__file__).parent.parent / "static" / "images" / "placeholder.jpg"
                if default_logo.exists():
                    import shutil
                    shutil.copy(default_logo, logo_path)
                    self.logger.info(f"Initialized default logo at {logo_path}")
            except Exception as e:
                self.logger.error(f"Failed to initialize default logo: {str(e)}")

    def display_idle_logo(self, profile_id: int = None) -> bool:
        """Display idle logo with enhanced error handling"""
        try:
            if not self._mpv_manager._mpv_ready:
                self.logger.warning("MPV not ready in display_idle_logo")
                return False

            if profile_id:
                self.logger.info(f"Applying profile {profile_id} for idle logo")
                if not self._mpv_manager.apply_profile(profile_id):
                    self.logger.error(f"Failed to apply profile {profile_id}")
                    return False

            try:
                logo_path = self._validate_logo_file()
                self.logger.info(f"Using logo at: {logo_path}")
            except Exception as e:
                self.logger.error(f"Logo validation failed: {str(e)}")
                return False

            stop_res = self._mpv_manager._send_command({"command": ["stop"]})
            if not stop_res or 'error' in stop_res:
                self.logger.warning("Failed to stop current playback")

            load_success = False
            for attempt in range(2):
                load_res = self._mpv_manager._send_command({
                    "command": ["loadfile", str(logo_path), "replace"]
                })
                
                if load_res and 'error' not in load_res:
                    load_success = True
                    break
                self.logger.warning(f"Logo load failed (attempt {attempt + 1})")

            if not load_success:
                self.logger.error("Failed to load logo file after retries")
                return False

            props_set = True
            if not self._mpv_manager._send_command({"command": ["set_property", "loop", "inf"]}):
                self.logger.warning("Failed to set loop property")
                props_set = False
            
            if not self._mpv_manager._send_command({"command": ["set_property", "mute", "yes"]}):
                self.logger.warning("Failed to set mute property")
                props_set = False

            self._last_playback_state.update({
                'status': 'idle',
                'playlist_id': None,
                'timestamp': time.time()
            })
            self._update_playback_status(None, 'idle')
            
            self.socketio.emit('playback_state', {
                'status': 'idle',
                'settings': self._mpv_manager._current_settings
            })
            
            return props_set
            
        except Exception as e:
            self.logger.error(f"Logo display failed: {str(e)}", exc_info=True)
            return False

    def _validate_logo_file(self) -> Path:
        """Validate logo file exists and is accessible"""
        logo_path = self.upload_folder / PlaybackConstants.DEFAULT_LOGO
        
        if not logo_path.exists():
            self.logger.warning(f"Logo file not found at {logo_path}, trying default...")
            default_logo = Path(__file__).parent.parent / "static" / "images" / "placeholder.jpg"
            if default_logo.exists():
                import shutil
                try:
                    shutil.copy(default_logo, logo_path)
                    self.logger.info(f"Copied default logo to {logo_path}")
                    logo_path.chmod(0o664)
                    os.chown(logo_path, os.getuid(), os.getgid())
                except Exception as copy_error:
                    self.logger.error(f"Failed to copy default logo: {str(copy_error)}")
                    raise
            else:
                raise FileNotFoundError("Default logo file not found in static/images")

        if not os.access(logo_path, os.R_OK):
            self.logger.error(f"Insufficient permissions to read logo file: {logo_path}")
            try:
                logo_path.chmod(0o664)
            except Exception as perm_error:
                self.logger.error(f"Failed to fix permissions: {str(perm_error)}")
                raise

        return logo_path

    def get_current_logo_path(self) -> Path:
        """Safely get current logo path"""
        try:
            return self._validate_logo_file()
        except FileNotFoundError:
            return Path(__file__).parent.parent / "static" / "images" / "placeholder.jpg"

    def get_current_logo_status(self) -> dict:
        """Get complete logo status for API"""
        try:
            path = self._validate_logo_file()
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

    def _update_playback_status(self, playlist_id: Optional[int], status: str):
        """Update playback status in database"""
        from ..models import PlaybackStatus
        
        playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
        playback.playlist_id = playlist_id
        playback.status = status
        self.db_session.add(playback)
        self.db_session.commit()
