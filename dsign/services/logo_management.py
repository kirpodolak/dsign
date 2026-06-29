import os
import time
from pathlib import Path
from typing import Dict, Optional
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from flask import current_app

from .playback_constants import PlaybackConstants
from .logo_viewer import LogoViewer

class LogoManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self._mpv_manager = mpv_manager
        self._last_playback_state = {}
        self._logo_viewer = LogoViewer(logger=logger)

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

    def _transition_mode(self) -> str:
        mode = (os.getenv("DSIGN_PLAYLIST_TRANSITION") or "logo").strip().lower()
        if mode not in ("logo", "black", "none"):
            return "logo"
        return mode

    def display_playlist_transition(self) -> bool:
        """
        Short placeholder between playlist items (streams): idle logo or black frame.
        Wayland/labwc: compositor keeps imv logo underneath; no MPV placeholder needed.
        """
        if PlaybackConstants.is_wayland_backend():
            return True
        mode = self._transition_mode()
        if mode == "none":
            return True
        if mode == "black":
            return self._load_transition_black()
        return self._load_transition_logo(loop=False)

    def _load_transition_black(self) -> bool:
        black_src = (os.getenv("DSIGN_TRANSITION_BLACK_SRC") or "").strip()
        if not black_src:
            black_src = "lavfi://color=c=black:s=1920x1080:r=24"
        commands = [
            ["loadfile", black_src, "replace"],
            ["set_property", "loop-file", "no"],
            ["set_property", "pause", "no"],
        ]
        for cmd in commands:
            response = self._mpv_manager._send_command({"command": cmd}, timeout=8.0)
            if not response or response.get("error") != "success":
                self.logger.debug(f"Transition black command failed: {' '.join(map(str, cmd))}")
                return False
        return True

    def show_between_items_frame(self) -> None:
        """
        Best-effort black frame between playlist items (DRM mode only).
        Wayland: imv logo stays visible under MPV; no lavf black flash.
        """
        if PlaybackConstants.is_wayland_backend():
            return
        black_src = (os.getenv("DSIGN_TRANSITION_BLACK_SRC") or "").strip()
        if not black_src:
            black_src = "lavfi://color=c=black:s=1920x1080:r=24"
        try:
            timeout = float((os.getenv("DSIGN_MPV_TRANSITION_TIMEOUT_SEC") or "2.5").strip())
        except ValueError:
            timeout = 2.5
        timeout = max(1.0, min(8.0, timeout))
        self._mpv_manager._send_command(
            {"command": ["loadfile", black_src, "replace"]},
            timeout=timeout,
            max_attempts=1,
        )

    def _load_transition_logo(self, *, loop: bool) -> bool:
        logo_path = self._validate_logo_file()
        if not logo_path:
            return self._load_transition_black()

        commands = [
            ["loadfile", str(logo_path), "replace"],
            ["set_property", "loop-file", "inf" if loop else "no"],
            ["set_property", "pause", "no"],
        ]
        for cmd in commands:
            response = self._mpv_manager._send_command(
                {"command": cmd},
                timeout=5.0,
                max_attempts=1,
            )
            if not response or response.get("error") != "success":
                self.logger.warning(f"Failed command: {' '.join(map(str, cmd))}")
                return False

        self._mpv_manager._send_command(
            {"command": ["set_property", "panscan", 0.0]},
            timeout=2.0,
            max_attempts=1,
        )
        return True

    def prepare_audio_playback(self) -> Dict[str, str]:
        """
        C2: audio-only item — logo visible while MPV plays audio only.
        Wayland: vo=null so imv underneath shows through; DRM: external-file logo image.
        """
        opts: Dict[str, str] = {"vid": "no", "keep-open": "no"}
        if PlaybackConstants.is_wayland_backend():
            try:
                self._mpv_manager.set_vo_property("null", timeout=3.0)
            except Exception as exc:
                self.logger.debug(
                    "Audio playback: vo=null failed",
                    extra={"error": str(exc)},
                )
            if self._logo_viewer.is_active() is False:
                self._logo_viewer.reload()
            return opts
        try:
            logo_path = self._validate_logo_file()
            opts["external-file"] = str(logo_path)
        except Exception as exc:
            self.logger.warning(
                "Audio playback: logo unavailable for external-file",
                extra={"error": str(exc)},
            )
        return opts

    def restore_after_audio_playback(self) -> None:
        """Restore MPV video output after audio-only (Wayland vo=gpu)."""
        if not PlaybackConstants.is_wayland_backend():
            return
        vo = (os.getenv("DSIGN_MPV_VO") or "gpu").strip() or "gpu"
        try:
            self._mpv_manager.set_vo_property(vo, timeout=5.0)
        except Exception as exc:
            self.logger.debug(
                "Audio playback: restore vo failed",
                extra={"error": str(exc), "vo": vo},
            )

    def display_idle_logo(self) -> bool:
        if PlaybackConstants.is_wayland_backend():
            try:
                self._mpv_manager._send_command(
                    {"command": ["stop"]},
                    timeout=3.0,
                    max_attempts=1,
                )
            except Exception as exc:
                self.logger.warning(
                    "Wayland idle: MPV stop failed (logo still visible via imv)",
                    extra={"error": str(exc)},
                )
            return True
        return self._load_transition_logo(loop=True)

    def _send_ipc_command(self, command: Dict, timeout: float = 2.0) -> bool:
        """Safe IPC command sending with retries and timeout"""
        for attempt in range(3):
            try:
                response = self._mpv_manager._send_command(command)
                if response and response.get('error') == 'success':
                    return True
                time.sleep(0.1 * (attempt + 1))
            except Exception as e:
                self.logger.warning(f"IPC attempt {attempt + 1} failed: {command} - {str(e)}")
                time.sleep(0.2)
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
            
    def _verify_logo_displayed(self, timeout: float = 3.0) -> bool:
        """Проверка с таймаутом и повторными попытками"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self._mpv_manager._send_command({
                    "command": ["get_property", "filename"]
                })
                if response and str(self._validate_logo_file()) in response.get("data", ""):
                    return True
                time.sleep(0.3)
            except Exception:
                time.sleep(0.3)
        return False
            
    def restart_idle_logo(self, upload_folder=None, idle_logo=None, rotate: Optional[int] = None):
        """Refresh idle logo. Wayland: restart imv; DRM: loadfile in MPV."""
        if PlaybackConstants.is_wayland_backend():
            self._initialize_default_logo()
            if self._logo_viewer.reload():
                return True
            self.logger.warning("Wayland logo viewer reload failed; imv may still show old file")
            return True
        try:
            # Установка значений по умолчанию из конфига
            upload_folder = upload_folder or current_app.config['UPLOAD_FOLDER']
            idle_logo = idle_logo or current_app.config['IDLE_LOGO']
            
            logo_path = Path(upload_folder) / idle_logo

            resp = self._mpv_manager._send_command(
                {"command": ["loadfile", str(logo_path), "replace"]},
                timeout=5.0
            )
            if not resp or resp.get("error") != "success":
                raise RuntimeError("Failed to load idle logo in MPV")

            # Apply known-good settings for idle logo
            self._mpv_manager._send_command({"command": ["set_property", "loop-file", "inf"]}, timeout=2.0)
            self._mpv_manager._send_command({"command": ["set_property", "pause", "no"]}, timeout=2.0)
            self._mpv_manager._send_command(
                {"command": ["set_property", "panscan", 0.0]},
                timeout=2.0,
            )
            if rotate is not None and int(rotate) in (0, 90, 180, 270):
                self._mpv_manager._send_command(
                    {"command": ["set_property", "video-rotate", int(rotate)]},
                    timeout=2.0,
                )
            return True

        except Exception as e:
            self.logger.error(f"Ошибка обновления логотипа: {str(e)}")
            return False

    def _get_mpv_current_file(self) -> Optional[str]:
        """Получает текущий файл из MPV через IPC"""
        response = self._send_ipc_command({
            "command": ["get_property", "filename"]
        })
        return response.get("data") if response else None
            
    def _get_logo_path(self) -> Path:
        """Возвращает полный путь к файлу логотипа"""
        return Path(current_app.config['UPLOAD_FOLDER']) / current_app.config['IDLE_LOGO']
