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


    def _send_ipc_command(self, command: Dict, timeout: float = 2.0) -> Optional[Dict]:
        """Safe IPC command sending with retries and timeout - возвращает response или None"""
        for attempt in range(3):
            try:
                # Добавляем целочисленный request_id
                command_with_id = command.copy()
                command_with_id["request_id"] = int(time.time() * 1000)
                
                response = self._mpv_manager._send_command(command_with_id)
                # Возвращаем полный response, а не булево значение
                return response
            except Exception as e:
                self.logger.warning(f"IPC attempt {attempt + 1} failed: {command} - {str(e)}")
                time.sleep(0.2)
        return None

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
            
    def restart_idle_logo(self, upload_folder=None, idle_logo=None):
        """Обновляет логотип с проверкой через метаданные файла"""
        try:
            # Установка значений по умолчанию из конфига
            upload_folder = upload_folder or current_app.config['UPLOAD_FOLDER']
            idle_logo = idle_logo or current_app.config['IDLE_LOGO']
            
            logo_path = Path(upload_folder) / idle_logo
            
            # 1. Получаем текущие метаданные файла
            initial_stat = os.stat(logo_path)
            
            # 2. Отправляем команду замены с целочисленным request_id
            cmd = {
                "command": ["loadfile", str(logo_path), "replace"],
                "request_id": int(time.time() * 1000)  # Целое число
            }
            response = self._send_ipc_command(cmd, timeout=2.0)
            
            # Исправляем проверку ответа - response теперь объект или None
            if not response or response.get("error") != "success":
                raise RuntimeError("MPV не подтвердил обновление логотипа")
            
            # 3. Проверяем изменение файла (3 попытки)
            for _ in range(3):
                time.sleep(0.3)
                current_stat = os.stat(logo_path)
                if current_stat.st_mtime != initial_stat.st_mtime or \
                   current_stat.st_size != initial_stat.st_size:
                    self.logger.info("Файл логотипа изменен (mtime или size изменился)")
                    return True
            
            # 4. Дополнительная проверка через MPV
            mpv_file = self._get_mpv_current_file()
            if mpv_file and mpv_file.endswith(idle_logo):
                self.logger.info("MPV подтверждает отображение логотипа")
                return True
                
            raise RuntimeError("Не удалось подтвердить обновление логотипа")

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
