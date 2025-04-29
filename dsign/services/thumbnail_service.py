import os
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple
from threading import Lock
from PIL import Image, UnidentifiedImageError
from flask import current_app
from dsign.config import UPLOAD_FOLDER, THUMBNAIL_FOLDER, THUMBNAIL_URL, DEFAULT_LOGO  # Измененный импорт

class ThumbnailService:
    def __init__(
        self,
        upload_folder: str = UPLOAD_FOLDER,
        thumbnail_folder: str = THUMBNAIL_FOLDER,
        thumbnail_url: str = THUMBNAIL_URL,
        default_thumbnail: str = DEFAULT_LOGO,
        thumbnail_size: Tuple[int, int] = (300, 300),
        ffmpeg_timeout: int = 15
    ):
        """
        Инициализация сервиса миниатюр с интеграцией конфигурации.

        Args:
            upload_folder: Папка с оригинальными файлами (из конфига)
            thumbnail_folder: Папка для хранения миниатюр (из конфига)
            thumbnail_url: Базовый URL для доступа к миниатюрам (из конфига)
            default_thumbnail: Имя файла с дефолтной миниатюрой (из конфига)
            thumbnail_size: Размер генерируемых миниатюр (ширина, высота)
            ffmpeg_timeout: Таймаут генерации видео-превью в секундах
        """
        self.upload_folder = Path(upload_folder)
        self.thumbnail_folder = Path(thumbnail_folder)
        self.thumbnail_url = thumbnail_url
        self.thumbnail_size = thumbnail_size
        self.default_thumbnail = default_thumbnail
        self.ffmpeg_timeout = ffmpeg_timeout
        self.lock = Lock()
        self.ffmpeg_available = self._check_ffmpeg()
        
        # Создание необходимых директорий
        self._ensure_dirs()
        
        current_app.logger.info(
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
            current_app.logger.error(f"Failed to create thumbnail directory: {str(e)}")
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
            current_app.logger.warning("FFmpeg is not available. Video thumbnails will be disabled")
            return False

    def get_thumbnail_url(self, filename: str) -> str:
        """
        Возвращает полный URL для доступа к миниатюре.
        
        Args:
            filename: Исходное имя файла
            
        Returns:
            str: URL вида /media/thumbnails/thumb_filename.jpg
        """
        return f"{self.thumbnail_url}/thumb_{filename}"

    def generate_thumbnail(self, filename: str) -> Optional[Path]:
        """
        Генерирует и возвращает путь к миниатюре.
        
        Args:
            filename: Имя исходного файла
            
        Returns:
            Path: Путь к сгенерированной миниатюре или None при ошибке
        """
        file_path = self.upload_folder / filename
        if not file_path.exists():
            current_app.logger.warning(f"Source file not found: {filename}")
            return None

        thumb_path = self.thumbnail_folder / f"thumb_{filename}"
        
        # Проверка кэша перед генерацией
        if thumb_path.exists():
            return thumb_path

        with self.lock:  # Защита от race condition
            try:
                ext = file_path.suffix.lower()
                
                # Обработка изображений
                if ext in ('.jpg', '.jpeg', '.png', '.webp'):
                    return self._generate_image_thumbnail(file_path, thumb_path)
                
                # Обработка видео (только если ffmpeg доступен)
                elif ext in ('.mp4', '.avi', '.mov') and self.ffmpeg_available:
                    return self._generate_video_thumbnail(file_path, thumb_path)
                
                # Неподдерживаемый формат
                current_app.logger.warning(f"Unsupported file type for thumbnails: {filename}")
                return None
                
            except Exception as e:
                current_app.logger.error(f"Failed to generate thumbnail for {filename}: {str(e)}")
                return None

    def _generate_image_thumbnail(
        self, 
        source_path: Path, 
        thumb_path: Path
    ) -> Optional[Path]:
        """Генерирует миниатюру для изображения"""
        try:
            with Image.open(source_path) as img:
                # Сохраняем пропорции
                img.thumbnail(self.thumbnail_size)
                
                # Конвертируем в RGB если нужно (для PNG с альфа-каналом)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                img.save(thumb_path, quality=85, optimize=True)
                return thumb_path
                
        except UnidentifiedImageError:
            current_app.logger.error(f"Corrupted image file: {source_path.name}")
            return None
        except Exception as e:
            current_app.logger.error(f"Image thumbnail error: {str(e)}")
            return None

    def _generate_video_thumbnail(
        self, 
        source_path: Path, 
        thumb_path: Path,
        time_pos: str = "00:00:01"
    ) -> Optional[Path]:
        """Генерирует миниатюру для видео через FFmpeg"""
        try:
            cmd = [
                'ffmpeg',
                '-i', str(source_path),
                '-ss', time_pos,
                '-vframes', '1',
                '-q:v', '2',
                '-vf', f'scale={self.thumbnail_size[0]}:-1',
                '-y',  # Перезаписать если существует
                str(thumb_path)
            ]
            
            subprocess.run(
                cmd,
                check=True,
                timeout=self.ffmpeg_timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            return thumb_path if thumb_path.exists() else None
            
        except subprocess.TimeoutExpired:
            current_app.logger.error(f"Video thumbnail timeout for {source_path.name}")
            return None
        except subprocess.CalledProcessError as e:
            current_app.logger.error(
                f"FFmpeg error for {source_path.name}: {e.stderr.decode().strip()}"
            )
            return None

    def cleanup_thumbnails(self, max_age_days: int = 30) -> int:
        """
        Очищает старые миниатюры.
        
        Args:
            max_age_days: Максимальный возраст файлов в днях
            
        Returns:
            int: Количество удаленных файлов
        """
        deleted = 0
        now = time.time()
        cutoff = now - (max_age_days * 86400)
        
        try:
            for thumb_file in self.thumbnail_folder.glob('thumb_*'):
                if thumb_file.stat().st_mtime < cutoff:
                    thumb_file.unlink()
                    deleted += 1
                    
            current_app.logger.info(f"Cleaned up {deleted} old thumbnails")
            return deleted
            
        except Exception as e:
            current_app.logger.error(f"Thumbnail cleanup failed: {str(e)}")
            return 0

    def get_default_thumbnail_path(self) -> Path:
        """Возвращает путь к дефолтной миниатюре"""
        default_path = self.upload_folder / self.default_thumbnail
        
        if not default_path.exists():
            current_app.logger.critical("Default thumbnail image is missing!")
            raise FileNotFoundError(f"Default thumbnail not found at {default_path}")
            
        return default_path
