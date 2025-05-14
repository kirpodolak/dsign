import os
import logging
from werkzeug.utils import secure_filename
from flask import send_from_directory
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from PIL import Image
import io
from dsign.models import Playlist
from .logger import ServiceLogger

class FileService:
    ALLOWED_MEDIA_EXTENSIONS = {'jpg', 'jpeg', 'png', 'mp4', 'avi'}
    ALLOWED_LOGO_EXTENSIONS = {'jpg', 'jpeg', 'png'}
    DEFAULT_LOGO = 'idle_logo.jpg'
    MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2MB
    MAX_MEDIA_SIZE = 50 * 1024 * 1024  # 50MB
    THUMBNAIL_CACHE = {}  # Классовый кэш для миниатюр
    THUMBNAIL_SIZE = (200, 200)  # Размер миниатюры

    def __init__(
        self, 
        upload_folder: str, 
        logger: Optional[Union[logging.Logger, ServiceLogger]] = None,
        thumbnail_service: Optional['ThumbnailService'] = None  # Добавлено
    ):
        self.upload_folder = Path(upload_folder)
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self.thumbnail_service = thumbnail_service  # Добавлено
        self._ensure_directories()

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'module': 'FileService'}
        if extra:
            extra_data.update(extra)
        self.logger.error(message, exc_info=exc_info, extra=extra_data)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'module': 'FileService'}
        if extra:
            extra_data.update(extra)
        self.logger.info(message, extra=extra_data)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'module': 'FileService'}
        if extra:
            extra_data.update(extra)
        self.logger.warning(message, extra=extra_data)

    def _ensure_directories(self) -> None:
        """Создание необходимых директорий"""
        try:
            self.upload_folder.mkdir(exist_ok=True, parents=True)
            (self.upload_folder / 'logo').mkdir(exist_ok=True)
            (self.upload_folder / 'tmp').mkdir(exist_ok=True)
        except Exception as e:
            self._log_error(f"Failed to create directories: {str(e)}", extra={'action': 'create_directories'})
            raise RuntimeError("Failed to create upload directories")

    def allowed_file(self, filename: str, file_size: int = 0) -> bool:
        """
        Проверка допустимых расширений и размера для медиафайлов
        
        Args:
            filename: Имя файла
            file_size: Размер файла в байтах (опционально)
        """
        has_valid_extension = '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in self.ALLOWED_MEDIA_EXTENSIONS
        has_valid_size = file_size <= self.MAX_MEDIA_SIZE if file_size > 0 else True
        return has_valid_extension and has_valid_size

    def allowed_logo_file(self, filename: str, file_size: int = 0) -> bool:
        """
        Проверка допустимых расширений и размера для логотипов
        
        Args:
            filename: Имя файла
            file_size: Размер файла в байтах (опционально)
        """
        has_valid_extension = '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in self.ALLOWED_LOGO_EXTENSIONS
        has_valid_size = file_size <= self.MAX_LOGO_SIZE if file_size > 0 else True
        return has_valid_extension and has_valid_size

    def get_media_files(self, playlist_id: Optional[int] = None) -> List[dict]:
        """Получение списка медиафайлов с метаданными"""
        try:
            files = []
            for f in self.upload_folder.iterdir():
                if f.is_file() and f.suffix.lower()[1:] in self.ALLOWED_MEDIA_EXTENSIONS:
                    stat = f.stat()
                    files.append({
                        'filename': f.name,
                        'path': str(f.relative_to(self.upload_folder)),
                        'size': stat.st_size,
                        'modified': stat.st_mtime,
                        'type': f.suffix.lower()[1:],
                        'mimetype': self._get_mime_type(f.suffix.lower()[1:])
                    })
            return files
        except Exception as e:
            self._log_error(f"Failed to get media files: {str(e)}", extra={'action': 'get_media_files'})
            return []

    def serve_media_file(self, filename: str, thumb: bool = False):
        """
        Отдача медиафайла или его миниатюры
        
        Args:
            filename: Имя файла
            thumb: Если True - возвращает миниатюру
        """
        try:
            if thumb:
                thumb_path = self.get_media_thumbnail(filename)
                if not thumb_path:
                    raise FileNotFoundError(f"Thumbnail for {filename} not found")
                return send_from_directory(
                    os.path.dirname(thumb_path),
                    os.path.basename(thumb_path))
            
            file_path = os.path.join(self.upload_folder, filename)
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File {filename} not found")
        
            headers = {
                'X-File-Size': str(stat.st_size),
                'X-File-Modified': str(stat.st_mtime),
                'X-File-Type': os.path.splitext(filename)[1][1:].lower()
            }
            return send_from_directory(
                self.upload_folder,
                filename,
                headers=headers
            )
        except Exception as e:
            self._log_error(f"Failed to serve file {filename}: {str(e)}", 
                          extra={'filename': filename, 'action': 'serve_media_file'})
            raise

    def _get_mime_type(self, ext: str) -> str:
        """Вспомогательный метод для определения MIME-типа"""
        mime_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'mp4': 'video/mp4',
            'webm': 'video/webm'
        }
        return mime_map.get(ext, 'application/octet-stream')

    def handle_upload(self, files: List) -> List[str]:
        """Обработка загрузки файлов"""
        saved_files = []
        for file in files:
            if file and self.allowed_file(file.filename, file.content_length):
                try:
                    filename = secure_filename(file.filename)
                    file_path = self.upload_folder / filename
                    file.save(file_path)
                    saved_files.append(filename)
                    self._log_info(f"Successfully uploaded file: {filename}", 
                                 extra={'filename': filename, 'action': 'file_upload'})
                except Exception as e:
                    self._log_error(f"Failed to upload file {file.filename}: {str(e)}", 
                                  extra={'filename': file.filename, 'action': 'file_upload'})
        return saved_files

    def handle_logo_upload(self, logo) -> Dict[str, Any]:
        """
        Обработка загрузки логотипа с проверкой размера
        
        Returns:
            Dict: {
                'success': bool,
                'filename': str (если успешно),
                'error': str (если ошибка)
            }
        """
        if not logo:
            return {'success': False, 'error': 'No file provided'}
            
        if not self.allowed_logo_file(logo.filename, logo.content_length):
            error_msg = 'Invalid file type or size exceeds limit (max 2MB)'
            self._log_warning(error_msg, extra={
                'action': 'logo_upload',
                'filename': logo.filename,
                'file_size': logo.content_length
            })
            return {'success': False, 'error': error_msg}

        try:
            filename = self.DEFAULT_LOGO
            file_path = self.upload_folder / filename
            
            logo.save(file_path)
            self._log_info("Logo uploaded successfully", 
                         extra={'filename': filename, 'action': 'logo_upload'})
            
            return {
                'success': True,
                'filename': filename,
                'message': 'Logo updated successfully'
            }
        except Exception as e:
            error_msg = f"Failed to upload logo: {str(e)}"
            self._log_error(error_msg, extra={'action': 'logo_upload'})
            return {'success': False, 'error': error_msg}

    def delete_files(self, filenames: List[str]) -> Dict[str, Any]:
        """Удаление файлов"""
        deleted = []
        failed = []
        
        for filename in filenames:
            try:
                file_path = self.upload_folder / secure_filename(filename)
                if file_path.exists():
                    file_path.unlink()
                    deleted.append(filename)
                    self._log_info(f"Deleted file: {filename}", 
                                 extra={'filename': filename, 'action': 'delete_file'})
                else:
                    failed.append(filename)
            except Exception as e:
                failed.append(filename)
                self._log_error(f"Failed to delete file {filename}: {str(e)}", 
                               extra={'filename': filename, 'action': 'delete_file'})
        
        return {
            "deleted": deleted,
            "count": len(deleted),
            "failed": failed
        }

    def get_logo_path(self) -> str:
        """Получение пути к логотипу с проверкой существования"""
        logo_path = self.upload_folder / self.DEFAULT_LOGO
        if not logo_path.exists():
            raise FileNotFoundError(f"Custom logo not found at {logo_path}")
        return str(logo_path)

    def get_logo_size(self) -> int:
        """Получение размера логотипа в байтах"""
        logo_path = self.upload_folder / self.DEFAULT_LOGO
        if logo_path.exists():
            return logo_path.stat().st_size
        return 0
        
    def get_media_thumbnail(self, filename: str) -> Optional[str]:
        """Получение пути к миниатюре через ThumbnailService"""
        if not hasattr(self, 'thumbnail_service') or not self.thumbnail_service:
            self._log_warning("Thumbnail service not available", 
                            extra={'filename': filename})
            return None
    
        try:
            thumb_path = self.thumbnail_service.generate_thumbnail(filename)
            return str(thumb_path) if thumb_path else None
        except Exception as e:
            self._log_error(f"Thumbnail error: {str(e)}", 
                          extra={'filename': filename, 'action': 'generate_thumbnail'})
            return None
            
    def get_media_files_with_playlist_info(self, playlist_id=None, db_session=None):
        """Получает файлы с информацией о принадлежности к плейлисту"""
        try:
            all_files = self.get_media_files()
    
            if not playlist_id or playlist_id == 'all':
                return all_files
                
            if not db_session:
                raise RuntimeError("Database session not provided")
            
            playlist_files = set()
            playlist = db_session.query(Playlist).get(playlist_id)
            if playlist:
                playlist_files = {f.file_name for f in playlist.files}
            
            return [{
                **file,
                'included': file['filename'] in playlist_files,
                'is_video': file['type'].lower() in {'mp4', 'avi', 'webm', 'mov'}
            } for file in all_files]
            
        except Exception as e:
            self._log_error(f"Error in get_media_files_with_playlist_info: {str(e)}", 
                          extra={'playlist_id': playlist_id, 'action': 'get_playlist_files'})
            raise
