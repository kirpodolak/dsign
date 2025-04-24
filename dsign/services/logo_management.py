import os
import time
from pathlib import Path
from typing import Dict, Optional
from threading import Thread
from concurrent.futures import ThreadPoolExecutor

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
        """Initialize default logo in background"""
        Thread(target=self._async_initialize_logo).start()

    def _async_initialize_logo(self):
        """Async logo initialization"""
        logo_path = self.upload_folder / PlaybackConstants.DEFAULT_LOGO
        if not logo_path.exists():
            try:
                default_logo = Path(__file__).parent.parent / "static" / "images" / "placeholder.jpg"
                if default_logo.exists():
                    import shutil
                    shutil.copy(default_logo, logo_path)
                    logo_path.chmod(0o664)
                    self.logger.info(f"Initialized default logo at {logo_path}")
            except Exception as e:
                self.logger.error(f"Failed to initialize default logo: {str(e)}")

    def display_idle_logo(self) -> bool:
        logo_path = self._validate_logo_file()
        if not logo_path:
            return False

        settings = {
            "vo": "drm",
            "hwdec": "no",
            "loop-file": "inf",
            "pause": "no"
        }

        # Преобразуем путь в строку
        commands = [
            ["loadfile", str(logo_path), "replace"]
        ] + [["set", key, value] for key, value in settings.items()]

        for cmd in commands:
            response = self._mpv_manager._send_command({"command": cmd})
            if not response or response.get("error") != "success":
                self.logger.warning(f"Failed command: {' '.join(map(str, cmd))}")
                return False

        return True

    def _validate_logo_file(self) -> Path:
        """Проверка доступности файла логотипа"""
        logo_path = self.upload_folder / PlaybackConstants.DEFAULT_LOGO
        
        if not logo_path.exists():
            self._handle_missing_logo(logo_path)

        if not os.access(logo_path, os.R_OK):
            self._fix_logo_permissions(logo_path)

        return logo_path


    def _send_ipc_command(self, command: Dict) -> bool:
        """Safe IPC command sending with retries"""
        try:
            response = self._mpv_manager._send_command(command)
            return response and response.get('error') == 'success'
        except Exception as e:
            self.logger.warning(f"IPC command failed: {command} - {str(e)}")
            return False

    def _validate_logo_file(self) -> Path:
        """Validate logo file with improved error handling"""
        logo_path = self.upload_folder / PlaybackConstants.DEFAULT_LOGO
        
        if not logo_path.exists():
            self._handle_missing_logo(logo_path)

        if not os.access(logo_path, os.R_OK):
            self._fix_logo_permissions(logo_path)

        return logo_path

    def _handle_missing_logo(self, logo_path: Path):
        """Handle missing logo file scenario"""
        default_logo = Path(__file__).parent.parent / "static" / "images" / "placeholder.jpg"
        if not default_logo.exists():
            raise FileNotFoundError("Default logo file not found in static/images")

        try:
            import shutil
            shutil.copy(default_logo, logo_path)
            logo_path.chmod(0o664)
            self.logger.info(f"Copied default logo to {logo_path}")
        except Exception as e:
            self.logger.error(f"Failed to copy default logo: {str(e)}")
            raise

    def _fix_logo_permissions(self, logo_path: Path):
        """Fix logo file permissions"""
        try:
            logo_path.chmod(0o664)
            if os.getuid() != 0:  # Skip if not root
                os.chown(logo_path, os.getuid(), os.getgid())
        except Exception as e:
            self.logger.error(f"Failed to fix permissions: {str(e)}")
            raise

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
            
    def _get_playback_status(self) -> Dict:
        """Get current playback status from MPV"""
        try:
            response = self._mpv_manager._send_command({
                "command": ["get_property", "filename"]
            })
            return {
                "filename": response.get("data", ""),
                "status": "playing" if not response.get("error") else "error"
            }
        except Exception as e:
            self.logger.error(f"Failed to get playback status: {str(e)}")
            return {"filename": "", "status": "error"}
            
    def _verify_logo_displayed(self):
        """Проверка что логотип действительно отображается"""
        try:
            response = self._mpv_manager._send_command({
                "command": ["get_property", "filename"]
            })
            return response and "idle_logo" in response.get("data", "")
        except Exception:
            return False
