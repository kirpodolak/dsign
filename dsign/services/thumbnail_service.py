from typing import Optional
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

class ThumbnailService:
    def __init__(
        self,
        upload_folder: str = UPLOAD_FOLDER,
        thumbnail_folder: str = THUMBNAIL_FOLDER,
        thumbnail_url: str = THUMBNAIL_URL,
        default_thumbnail: str = DEFAULT_LOGO,
        thumbnail_size: tuple = (300, 300),
        ffmpeg_timeout: int = 15,
        logger=None
    ):
        self.upload_folder = Path(upload_folder)
        self.thumbnail_folder = Path(thumbnail_folder)
        self.thumbnail_url = thumbnail_url
        self.thumbnail_size = thumbnail_size
        self.default_thumbnail = default_thumbnail
        self.ffmpeg_timeout = ffmpeg_timeout
        self.logger = logger or logging.getLogger(__name__)
        self.lock = Lock()
        self.ffmpeg_available = self._check_ffmpeg()
        self._ensure_dirs()
        
        # Используем self.logger вместо current_app.logger
        self.logger.info(
            f"ThumbnailService initialized. "
            f"Upload folder: {self.upload_folder}, "
            f"Thumbnail folder: {self.thumbnail_folder}, "
            f"FFmpeg: {'available' if self.ffmpeg_available else 'not available'}"
        )

    def _ensure_dirs(self) -> None:
        """Создает необходимые директории"""
        try:
            self.thumbnail_folder.mkdir(exist_ok=True, parents=True)
        except Exception as e:
            self.logger.error(f"Failed to create thumbnail directory: {str(e)}")
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
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            self.logger.warning("FFmpeg is not available. Video thumbnails will be disabled")
            return False

    def get_thumbnail_url(self, filename: str) -> str:
        """
        Возвращает полный URL для доступа к миниатюре.
        """
        return f"{self.thumbnail_url}/thumb_{filename}"

    def generate_thumbnail(self, filename: str) -> Optional[Path]:
        """
        Генерирует и возвращает путь к миниатюре.
        """
        file_path = self.upload_folder / filename
        if not file_path.exists():
            self.logger.warning(f"Source file not found: {filename}")
            return None

        thumb_path = self.thumbnail_folder / f"thumb_{filename}"
        
        if thumb_path.exists():
            return thumb_path

        with self.lock:
            try:
                ext = file_path.suffix.lower()
                
                if ext in ('.jpg', '.jpeg', '.png', '.webp'):
                    return self._generate_image_thumbnail(file_path, thumb_path)
                elif ext in ('.mp4', '.avi', '.mov') and self.ffmpeg_available:
                    return self._generate_video_thumbnail(file_path, thumb_path)
                
                self.logger.warning(f"Unsupported file type for thumbnails: {filename}")
                return None
                
            except Exception as e:
                self.logger.error(f"Failed to generate thumbnail for {filename}: {str(e)}")
                return None

    def _generate_image_thumbnail(self, source_path: Path, thumb_path: Path) -> Optional[Path]:
        """Генерирует миниатюру для изображения"""
        try:
            with Image.open(source_path) as img:
                img.thumbnail(self.thumbnail_size)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                img.save(thumb_path, quality=85, optimize=True)
                return thumb_path
                
        except UnidentifiedImageError:
            self.logger.error(f"Corrupted image file: {source_path.name}")
            return None
        except Exception as e:
            self.logger.error(f"Image thumbnail error: {str(e)}")
            return None

    def _generate_video_thumbnail(self, source_path: Path, thumb_path: Path) -> Optional[Path]:
        """Generate video thumbnail with multiple fallback strategies"""
        attempts = [
            {"time": "00:00:01", "scale": f"scale={self.thumbnail_size[0]}:-1"},
            {"time": "00:00:00", "scale": f"scale={self.thumbnail_size[0]}:-1"},
            {"time": "00:00:05", "scale": "scale=iw*sar:ih"},  # Try different scaling
        ]
    
        for attempt in attempts:
            try:
                cmd = [
                    'ffmpeg',
                    '-i', str(source_path),
                    '-ss', attempt["time"],
                    '-vframes', '1',
                    '-q:v', '2',
                    '-vf', attempt["scale"],
                    '-y',
                    str(thumb_path)
                ]
            
                result = subprocess.run(
                    cmd,
                    check=False,
                    timeout=self.ffmpeg_timeout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            
                # Verify the thumbnail was created and is valid
                if thumb_path.exists() and thumb_path.stat().st_size > 1024:
                    try:
                        with Image.open(thumb_path) as img:
                            img.verify()
                        return thumb_path
                    except Exception:
                        continue
                        
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Timeout generating thumbnail for {source_path.name}")
                continue
            except Exception as e:
                self.logger.warning(f"Attempt failed for {source_path.name}: {str(e)}")
                continue
    
        self.logger.error(f"All thumbnail generation attempts failed for {source_path.name}")
        return None
            
    def cleanup_thumbnails(self, max_age_days: int = 30) -> int:
        """Очищает старые миниатюры."""
        deleted = 0
        now = time.time()
        cutoff = now - (max_age_days * 86400)
        
        try:
            for thumb_file in self.thumbnail_folder.glob('thumb_*'):
                if thumb_file.stat().st_mtime < cutoff:
                    thumb_file.unlink()
                    deleted += 1
                    
            self.logger.info(f"Cleaned up {deleted} old thumbnails")
            return deleted
            
        except Exception as e:
            self.logger.error(f"Thumbnail cleanup failed: {str(e)}")
            return 0

    def get_default_thumbnail_path(self) -> Path:
        """Возвращает путь к дефолтной миниатюре"""
        default_path = self.upload_folder / self.default_thumbnail
        
        if not default_path.exists():
            self.logger.critical("Default thumbnail image is missing!")
            raise FileNotFoundError(f"Default thumbnail not found at {default_path}")
            
        return default_path
