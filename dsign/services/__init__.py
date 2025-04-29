"""/dsign/service/__init__.py Модуль инициализации сервисов"""

from typing import Dict, Any
import logging
from pathlib import Path

# Импортируем все сервисы
from .file_service import FileService
from .playback_service import PlaybackService
from .playlist_service import PlaylistService
from .settings_service import SettingsService
from .auth import AuthService
from .sockets import SocketService
from .thumbnail_service import ThumbnailService

class ServiceFactory:
    """Фабрика для инициализации сервисов"""
    
    @staticmethod
    def create_file_service(upload_folder: str, logger: logging.Logger = None):
        """Создает экземпляр FileService"""
        return FileService(upload_folder, logger)

    @staticmethod
    def create_thumbnail_service(
        upload_folder: str,
        thumbnail_folder: str,
        thumbnail_url: str,
        default_thumbnail: str,
        logger: logging.Logger = None
    ):
        """Создает экземпляр ThumbnailService"""
        return ThumbnailService(
            upload_folder=upload_folder,
            thumbnail_folder=thumbnail_folder,
            thumbnail_url=thumbnail_url,
            default_thumbnail=default_thumbnail,
            logger=logger
        )

    @staticmethod
    def create_playback_service(upload_folder: str, db, socketio, logger: logging.Logger = None):
        """Создает экземпляр PlaybackService"""
        try:
            return PlaybackService(
                upload_folder=upload_folder,
                db_session=db,
                socketio=socketio,
                logger=logger or logging.getLogger(__name__)
            )
        except Exception as e:
            if logger:
                logger.error(f"Failed to initialize playback service: {str(e)}")
            return None
        
    @staticmethod
    def create_playlist_service(db, logger: logging.Logger = None):
        """Создает экземпляр PlaylistService"""
        return PlaylistService(db, logger)

    @staticmethod
    def create_settings_service(settings_file: str, upload_folder: str, logger: logging.Logger = None):
        """Создает экземпляр SettingsService"""
        return SettingsService(settings_file, upload_folder, logger)

    @staticmethod
    def create_auth_service(secret_key: str, logger: logging.Logger = None):
        """Создает экземпляр AuthService"""
        return AuthService(secret_key, logger)

    @staticmethod
    def create_socket_service(socketio, db, logger: logging.Logger = None):
        """Создает экземпляр SocketService"""
        return SocketService(socketio, db, logger)

def init_services(config: Dict[str, Any], db, socketio=None, logger: logging.Logger = None) -> Dict[str, Any]:
    """Инициализация всех сервисов"""
    upload_folder = config.get('UPLOAD_FOLDER', str(Path.cwd() / 'media'))
    settings_file = config.get('SETTINGS_FILE', 'settings.json')
    secret_key = config['SECRET_KEY']

    services = {
        'file_service': ServiceFactory.create_file_service(
            upload_folder=upload_folder,
            logger=logger
        ),
        'thumbnail_service': ServiceFactory.create_thumbnail_service(
            upload_folder=upload_folder,
            thumbnail_folder=config.get('THUMBNAIL_FOLDER', str(Path(upload_folder) / 'thumbnails')),
            thumbnail_url=config.get('THUMBNAIL_URL', '/media/thumbnails'),
            default_thumbnail=config.get('DEFAULT_LOGO', 'default-preview.jpg'),
            logger=logger
        ),
        'playback_service': ServiceFactory.create_playback_service(
            upload_folder=upload_folder,
            db=db,
            socketio=socketio,
            logger=logger
        ),
        'playlist_service': ServiceFactory.create_playlist_service(
            db=db,
            logger=logger
        ),
        'settings_service': ServiceFactory.create_settings_service(
            settings_file=settings_file,
            upload_folder=upload_folder,
            logger=logger
        ),
        'auth_service': ServiceFactory.create_auth_service(
            secret_key=secret_key,
            logger=logger
        ),
        'db': db
    }
    
    if socketio:
        services['socket_service'] = ServiceFactory.create_socket_service(
            socketio=socketio,
            db=db,
            logger=logger
        )
    
    return services

__all__ = [
    'ServiceFactory',
    'init_services',
    'FileService',
    'PlaybackService',
    'PlaylistService',
    'SettingsService',
    'AuthService',
    'SocketService',
    'ThumbnailService'
]
