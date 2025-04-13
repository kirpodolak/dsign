"""/dsign/service/__init__.py Модуль инициализации сервисов"""

from typing import Dict, Any
import logging
from pathlib import Path
from .file_service import FileService
from .playback_service import PlaybackService

class ServiceFactory:
    """Фабрика для инициализации сервисов"""
    
    @staticmethod
    def create_file_service(upload_folder: str, logger: logging.Logger = None) -> 'FileService':
        """Создает экземпляр FileService"""
        from .file_service import FileService
        return FileService(upload_folder, logger)

    @staticmethod
    def create_playback_service(upload_folder: str, db, socketio, logger: logging.Logger = None) -> 'PlaybackService':
        """Создает экземпляр PlaybackService"""
        from .playback_service import PlaybackService
        return PlaybackService(
            upload_folder=upload_folder,
            db_session=db,
            socketio=socketio,
            logger=logger or logging.getLogger(__name__)
        )

    @staticmethod
    def create_playlist_service(db, logger: logging.Logger = None) -> 'PlaylistService':
        """Создает экземпляр PlaylistService"""
        from .playlist_service import PlaylistService
        return PlaylistService(db, logger)

    @staticmethod
    def create_settings_service(settings_file: str, upload_folder: str, logger: logging.Logger = None) -> 'SettingsService':
        """Создает экземпляр SettingsService"""
        from .settings_service import SettingsService
        return SettingsService(settings_file, upload_folder, logger)

    @staticmethod
    def create_auth_service(secret_key: str, logger: logging.Logger = None) -> 'AuthService':
        """Создает экземпляр AuthService"""
        from .auth import AuthService
        return AuthService(secret_key, logger)

    @staticmethod
    def create_socket_service(socketio, db, logger: logging.Logger = None) -> 'SocketService':
        """Создает экземпляр SocketService"""
        from .sockets import SocketService
        return SocketService(socketio, db, logger)

def init_services(config: Dict[str, Any], db, socketio=None, logger: logging.Logger = None) -> Dict[str, Any]:
    """Инициализация всех сервисов"""
    services = {
        'file_service': ServiceFactory.create_file_service(
            config.get('UPLOAD_FOLDER', str(Path.cwd() / 'media')),
            logger
        ),
        'playback_service': ServiceFactory.create_playback_service(
            config.get('UPLOAD_FOLDER', str(Path.cwd() / 'media')),
            db,
            socketio,
            logger
        ),
        'playlist_service': ServiceFactory.create_playlist_service(db, logger),
        'settings_service': ServiceFactory.create_settings_service(
            config.get('SETTINGS_FILE', 'settings.json'),
            config.get('UPLOAD_FOLDER', str(Path.cwd() / 'media')),
            logger
        ),
        'auth_service': ServiceFactory.create_auth_service(
            config['SECRET_KEY'],
            logger
        ),
        'db': db
    }
    
    if socketio:
        services['socket_service'] = ServiceFactory.create_socket_service(
            socketio, db, logger
        )
    
    return services

__all__ = [
    'ServiceFactory',
    'init_services',
    "FileService", 
    "PlaybackService"
]
