"""/dsign/service/__init__.py Модуль инициализации сервисов"""

from typing import Dict, Any, Optional
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
    def create_file_service(upload_folder: str, logger: logging.Logger = None) -> Optional[FileService]:
        try:
            return FileService(upload_folder, logger)
        except Exception as e:
            if logger:
                logger.error(f"FileService init error: {str(e)}")
            return None

    @staticmethod
    def create_thumbnail_service(
        upload_folder: str,
        thumbnail_folder: str,
        thumbnail_url: str,
        default_thumbnail: str,
        logger: logging.Logger = None
    ) -> Optional[ThumbnailService]:
        try:
            return ThumbnailService(
                upload_folder=upload_folder,
                thumbnail_folder=thumbnail_folder,
                thumbnail_url=thumbnail_url,
                default_thumbnail=default_thumbnail,
                logger=logger
            )
        except Exception as e:
            if logger:
                logger.error(f"ThumbnailService init error: {str(e)}")
            return None

    @staticmethod
    def create_playback_service(
        upload_folder: str, 
        db, 
        socketio, 
        logger: logging.Logger = None
    ) -> Optional[PlaybackService]:
        try:
            return PlaybackService(
                upload_folder=upload_folder,
                db_session=db,
                socketio=socketio,
                logger=logger or logging.getLogger(__name__)
            )
        except Exception as e:
            if logger:
                logger.error(f"PlaybackService init error: {str(e)}")
            return None
        
    @staticmethod
    def create_playlist_service(db, logger: logging.Logger = None) -> Optional[PlaylistService]:
        try:
            return PlaylistService(db, logger)
        except Exception as e:
            if logger:
                logger.error(f"PlaylistService init error: {str(e)}")
            return None

    @staticmethod
    def create_settings_service(
        settings_file: str, 
        upload_folder: str, 
        logger: logging.Logger = None
    ) -> Optional[SettingsService]:
        try:
            return SettingsService(settings_file, upload_folder, logger)
        except Exception as e:
            if logger:
                logger.error(f"SettingsService init error: {str(e)}")
            return None

    @staticmethod
    def create_auth_service(secret_key: str, logger: logging.Logger = None) -> Optional[AuthService]:
        try:
            return AuthService(secret_key, logger)
        except Exception as e:
            if logger:
                logger.error(f"AuthService init error: {str(e)}")
            return None

    @staticmethod
    def create_socket_service(socketio, db, logger: logging.Logger = None) -> Optional[SocketService]:
        try:
            return SocketService(socketio, db, logger)
        except Exception as e:
            if logger:
                logger.error(f"SocketService init error: {str(e)}")
            return None

def init_services(
    config: Dict[str, Any], 
    db, 
    socketio=None, 
    logger: logging.Logger = None
) -> Dict[str, Any]:
    logger = logger or logging.getLogger(__name__)
    
    try:
        # Проверка обязательных параметров конфигурации
        required_config = ['UPLOAD_FOLDER', 'SECRET_KEY']
        for key in required_config:
            if key not in config:
                raise ValueError(f"Missing required config key: {key}")

        services = {
            'file_service': FileService(
                upload_folder=config['UPLOAD_FOLDER'],
                logger=logger
            ),
            'thumbnail_service': ThumbnailService(
                upload_folder=config['UPLOAD_FOLDER'],
                thumbnail_folder=config.get('THUMBNAIL_FOLDER', str(Path(config['UPLOAD_FOLDER']) / 'thumbnails')),
                thumbnail_url=config.get('THUMBNAIL_URL', '/media/thumbnails'),
                default_thumbnail=config.get('DEFAULT_LOGO', 'default-preview.jpg'),
                logger=logger
            ),
            'db': db
        }

        # Инициализация остальных сервисов
        services.update({
            'playback_service': PlaybackService(
                upload_folder=config['UPLOAD_FOLDER'],
                db_session=db,
                socketio=socketio,
                logger=logger
            ),
            'playlist_service': PlaylistService(db.session, logger),
            'settings_service': SettingsService(
                settings_file=config.get('SETTINGS_FILE', 'settings.json'),
                upload_folder=config['UPLOAD_FOLDER'],
                logger=logger
            ),
            'auth_service': AuthService(config['SECRET_KEY'], logger)
        })

        if socketio:
            services['socket_service'] = SocketService(socketio, db, logger)

        logger.info("All services initialized successfully")
        return services

    except Exception as e:
        logger.critical(f"Failed to initialize services: {str(e)}", exc_info=True)
        raise RuntimeError(f"Services initialization failed: {str(e)}")

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
