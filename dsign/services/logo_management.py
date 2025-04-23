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
        """Display idle logo with MPV commands"""
        try:
            logo_path = self._validate_logo_file()
            self.logger.info(f"Displaying idle logo: {logo_path}")

            # Последовательность команд для MPV
            commands = [
                {"command": ["stop"]},
                {"command": ["loadfile", str(logo_path), "replace"]},
                {"command": ["set_property", "loop-file", "inf"]},
                {"command": ["set_property", "pause", "no"]}
            ]

            for cmd in commands:
                response = self._mpv_manager._send_command(cmd)
                if not response or response.get('error') != 'success':
                    self.logger.error(f"MPV command failed: {cmd} - {response}")
                    return False

            return True
        except Exception as e:
            self.logger.error(f"Failed to display idle logo: {str(e)}", exc_info=True)
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

    def _update_playback_state(self, status: str):
        """Update playback state"""
        self._update_playback_status(None, status)

    def _update_playback_status(self, playlist_id: Optional[int], status: str):
        """Update playback status in database"""
        from ..models import PlaybackStatus
        
        playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
        playback.playlist_id = playlist_id
        playback.status = status
        self.db_session.add(playback)
        self.db_session.commit()

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
