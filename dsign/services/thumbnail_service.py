from typing import Optional, Union, Dict, Any
from pathlib import Path
import logging
from threading import Lock
import subprocess
from PIL import Image, UnidentifiedImageError
from flask import current_app
import time
from dsign.config.config import (
    UPLOAD_FOLDER,
    THUMBNAIL_FOLDER,
    THUMBNAIL_URL,
    IDLE_LOGO as DEFAULT_LOGO
)
from dsign.services.logger import ServiceLogger

class ThumbnailService:
    def __init__(
        self,
        upload_folder: str = UPLOAD_FOLDER,
        thumbnail_folder: str = THUMBNAIL_FOLDER,
        thumbnail_url: str = THUMBNAIL_URL,
        default_thumbnail: str = DEFAULT_LOGO,
        thumbnail_size: tuple = (300, 300),
        ffmpeg_timeout: int = 15,
        logger: Optional[Union[logging.Logger, ServiceLogger]] = None
    ):
        self.upload_folder = Path(upload_folder)
        self.thumbnail_folder = Path(thumbnail_folder)
        self.thumbnail_url = thumbnail_url
        self.thumbnail_size = thumbnail_size
        self.default_thumbnail = default_thumbnail
        self.ffmpeg_timeout = ffmpeg_timeout
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self.lock = Lock()
        self.ffmpeg_available = self._check_ffmpeg()
        self._ensure_dirs()
        
        self.logger.info(
            "ThumbnailService initialized",
            extra={
                'upload_folder': str(self.upload_folder),
                'thumbnail_folder': str(self.thumbnail_folder),
                'ffmpeg_available': self.ffmpeg_available
            }
        )

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        log_extra = {'service': 'ThumbnailService'}
        if extra:
            log_extra.update({k: v for k, v in extra.items() if k != 'module'})  # Исключаем конфликтный ключ
        self.logger.error(message, exc_info=exc_info, extra=log_extra)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        log_extra = {'service': 'ThumbnailService'}
        if extra:
            log_extra.update({k: v for k, v in extra.items() if k != 'module'})  # Исключаем конфликтный ключ
        self.logger.info(message, extra=log_extra)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        log_extra = {'service': 'ThumbnailService'}
        if extra:
            log_extra.update({k: v for k, v in extra.items() if k != 'module'})  # Исключаем конфликтный ключ
        self.logger.warning(message, extra=log_extra)

    def _log_debug(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для отладочных логов"""
        log_extra = {'service': 'ThumbnailService'}
        if extra:
            log_extra.update({k: v for k, v in extra.items() if k != 'module'})  # Исключаем конфликтный ключ
        self.logger.debug(message, extra=log_extra)

    def _ensure_dirs(self) -> None:
        """Создает необходимые директории"""
        try:
            self.thumbnail_folder.mkdir(exist_ok=True, parents=True)
            self._log_info("Thumbnail directories verified")
        except Exception as e:
            self._log_error(
                f"Failed to create thumbnail directory: {str(e)}",
                extra={'action': 'ensure_directories'}
            )
            raise RuntimeError("Could not initialize thumbnail storage")

    def _check_ffmpeg(self) -> bool:
        """Проверяет доступность FFmpeg в системе"""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            available = result.returncode == 0
            self._log_info(
                f"FFmpeg {'available' if available else 'not available'}",
                {'action': 'check_ffmpeg'}  # Убрали 'module' из extra данных
            )
            return available
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self._log_warning(
                "FFmpeg is not available. Video thumbnails will be disabled",
                {'error': str(e), 'action': 'check_ffmpeg'}  # Убрали 'module' из extra данных
            )
            return False

    def get_thumbnail_url(self, filename: str) -> str:
        """Возвращает полный URL для доступа к миниатюре"""
        url = f"{self.thumbnail_url}/thumb_{filename}"
        self._log_debug(
            "Generated thumbnail URL",
            extra={'filename': filename, 'url': url}
        )
        return url

    def generate_thumbnail(self, filename: str) -> Optional[Path]:
        """Генерирует миниатюру с правильным расширением .jpg"""
        file_path = self.upload_folder / filename
        if not file_path.exists():
            self._log_warning(
                "Source file not found",
                extra={'filename': filename, 'action': 'generate_thumbnail'}
            )
            return None

        thumb_name = f"thumb_{Path(filename).stem}.jpg"
        thumb_path = self.thumbnail_folder / thumb_name
    
        if thumb_path.exists():
            self._log_debug(
                "Using existing thumbnail",
                extra={'filename': filename, 'thumb_path': str(thumb_path)}
            )
            return thumb_path

        with self.lock:
            try:
                ext = file_path.suffix.lower()
            
                if ext in ('.jpg', '.jpeg', '.png', '.webp'):
                    return self._generate_image_thumbnail(file_path, thumb_path)
                elif ext in ('.mp4', '.avi', '.mov'):
                    return self._generate_video_thumbnail(file_path, thumb_path)
            
                self._log_warning(
                    "Unsupported file type",
                    extra={'filename': filename, 'extension': ext}
                )
                return None
            
            except Exception as e:
                self._log_error(
                    "Failed to generate thumbnail",
                    extra={
                        'filename': filename,
                        'error': str(e),
                        'action': 'generate_thumbnail'
                    }
                )
                return None

    def _generate_image_thumbnail(self, source_path: Path, thumb_path: Path) -> Optional[Path]:
        """Генерирует миниатюру для изображения"""
        try:
            with Image.open(source_path) as img:
                img.thumbnail(self.thumbnail_size)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                img.save(thumb_path, quality=85, optimize=True)
                self._log_info(
                    "Image thumbnail generated",
                    extra={
                        'source_path': str(source_path),
                        'thumb_path': str(thumb_path),
                        'size': self.thumbnail_size
                    }
                )
                return thumb_path
                
        except UnidentifiedImageError as e:
            self._log_error(
                "Corrupted image file",
                extra={
                    'source_path': str(source_path),
                    'error': str(e),
                    'action': 'generate_image_thumbnail'
                }
            )
            return None
        except Exception as e:
            self._log_error(
                "Image thumbnail generation failed",
                extra={
                    'source_path': str(source_path),
                    'error': str(e),
                    'action': 'generate_image_thumbnail'
                }
            )
            return None

    def _generate_video_thumbnail(self, source_path: Path, thumb_path: Path) -> Optional[Path]:
        """Генерирует миниатюру для видео с явным указанием формата"""
        if not self.ffmpeg_available:
            self._log_warning(
                "FFmpeg not available - skipping video thumbnail",
                extra={'source_path': str(source_path)}
            )
            return None

        try:
            cmd = [
                'ffmpeg',
                '-i', str(source_path),
                '-ss', '00:00:01',
                '-vframes', '1',
                '-q:v', '2',
                '-vf', f'scale={self.thumbnail_size[0]}:-1',
                '-f', 'mjpeg',
                '-y',
                str(thumb_path)
            ]
        
            result = subprocess.run(
                cmd,
                check=True,
                timeout=self.ffmpeg_timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        
            if thumb_path.exists():
                try:
                    with Image.open(thumb_path) as img:
                        img.verify()
                    self._log_info(
                        "Video thumbnail generated",
                        extra={
                            'source_path': str(source_path),
                            'thumb_path': str(thumb_path),
                            'size': self.thumbnail_size
                        }
                    )
                    return thumb_path
                except Exception as e:
                    self._log_error(
                        "Invalid thumbnail image",
                        extra={
                            'thumb_path': str(thumb_path),
                            'error': str(e),
                            'action': 'verify_video_thumbnail'
                        }
                    )
                    thumb_path.unlink()
                
        except subprocess.CalledProcessError as e:
            self._log_error(
                "FFmpeg command failed",
                extra={
                    'command': ' '.join(cmd),
                    'error': e.stderr.decode(),
                    'action': 'generate_video_thumbnail'
                }
            )
        except Exception as e:
            self._log_error(
                "Video thumbnail generation failed",
                extra={
                    'source_path': str(source_path),
                    'error': str(e),
                    'action': 'generate_video_thumbnail'
                }
            )
    
        return None
            
    def cleanup_thumbnails(self, max_age_days: int = 30) -> int:
        """Очищает старые миниатюры"""
        deleted = 0
        now = time.time()
        cutoff = now - (max_age_days * 86400)
        
        try:
            for thumb_file in self.thumbnail_folder.glob('thumb_*'):
                if thumb_file.stat().st_mtime < cutoff:
                    thumb_file.unlink()
                    deleted += 1
                    
            self._log_info(
                "Thumbnails cleanup completed",
                extra={
                    'deleted_count': deleted,
                    'max_age_days': max_age_days
                }
            )
            return deleted
            
        except Exception as e:
            self._log_error(
                "Thumbnail cleanup failed",
                extra={
                    'error': str(e),
                    'action': 'cleanup_thumbnails'
                }
            )
            return 0

    def get_default_thumbnail_path(self) -> Path:
        """Возвращает путь к дефолтной миниатюре"""
        default_path = self.upload_folder / self.default_thumbnail
        
        if not default_path.exists():
            self._log_error(
                "Default thumbnail image is missing",
                extra={
                    'expected_path': str(default_path),
                    'action': 'get_default_thumbnail'
                }
            )
            raise FileNotFoundError(f"Default thumbnail not found at {default_path}")
            
        self._log_debug(
            "Default thumbnail path retrieved",
            extra={'path': str(default_path)}
        )
        return default_path
