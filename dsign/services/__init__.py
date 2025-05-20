"""
/dsign/service/__init__.py 
Модуль инициализации сервисов с централизованным логированием
"""
import io
import os
import sys
import platform
import traceback
import subprocess
from typing import Dict, Any, Optional
from pathlib import Path
from .logger import setup_logger

# Импортируем все сервисы
from .file_service import FileService
from .playback_service import PlaybackService
from .playlist_service import PlaylistService
from .settings_service import SettingsService
from .auth import AuthService
from .sockets import SocketService
from .thumbnail_service import ThumbnailService

class ServiceFactory:
    """Фабрика для инициализации сервисов с централизованным логированием"""
    
    @staticmethod
    def create_file_service(
        upload_folder: str, 
        thumbnail_service=None,
        logger=None
    ) -> Optional[FileService]:
        logger = logger or setup_logger('FileService')
        try:
            logger.info('Initializing FileService', {
                'upload_folder': upload_folder
            })
            return FileService(
                upload_folder=upload_folder,
                logger=logger,
                thumbnail_service=thumbnail_service
            )
        except Exception as e:
            logger.error('FileService initialization failed', {
                'error': str(e),
                'stack': True
            })
            return None

    @staticmethod
    def create_thumbnail_service(
        upload_folder: str,
        thumbnail_folder: str,
        thumbnail_url: str,
        default_thumbnail: str,
        logger=None
    ) -> Optional[ThumbnailService]:
        logger = logger or setup_logger('ThumbnailService')
        try:
            logger.info('Initializing ThumbnailService', {
                'upload_folder': upload_folder,
                'thumbnail_folder': thumbnail_folder,
                'thumbnail_url': thumbnail_url,
                'default_thumbnail': default_thumbnail
            })
        
            # Проверка существования директорий
            upload_path = Path(upload_folder)
            thumb_path = Path(thumbnail_folder)
        
            if not upload_path.exists():
                logger.error('Upload folder does not exist', {
                    'path': str(upload_path)
                })
                return None
            
            service = ThumbnailService(
                upload_folder=upload_folder,
                thumbnail_folder=thumbnail_folder,
                thumbnail_url=thumbnail_url,
                default_thumbnail=default_thumbnail,
                logger=logger
            )
        
            logger.info('ThumbnailService initialized successfully')
            return service
        
        except Exception as e:
            logger.error('ThumbnailService initialization failed', {
                'error': str(e),
                'stack': True,
                'exception_type': type(e).__name__
            })
            return None

    @staticmethod
    def create_playback_service(
        upload_folder: str, 
        db, 
        socketio=None, 
        logger=None
    ) -> Optional[PlaybackService]:
        """
        Создание сервиса воспроизведения
        :param upload_folder: Папка с медиафайлами
        :param db: Сессия базы данных
        :param socketio: Экземпляр SocketIO (опционально)
        :param logger: Логгер (опционально)
        :return: Экземпляр PlaybackService или None при ошибке
        """
        logger = logger or setup_logger('PlaybackService')
        try:
            logger.info('Initializing PlaybackService')
            
            # Проверка существования папки для загрузки
            upload_path = Path(upload_folder)
            if not upload_path.exists():
                raise RuntimeError(f"Upload folder does not exist: {upload_folder}")
            
            # Проверка доступности MPV
            try:
                mpv_check = subprocess.run(['which', 'mpv'], capture_output=True, text=True)
                if mpv_check.returncode != 0:
                    raise RuntimeError("MPV player not found in PATH")
                
                mpv_version = subprocess.run(['mpv', '--version'], capture_output=True, text=True)
                logger.info('MPV version check', {
                    'version': mpv_version.stdout.split('\n')[0] if mpv_version.stdout else 'unknown'
                })
            except Exception as e:
                logger.error('MPV check failed', {
                    'error': str(e),
                    'stack': True
                })
                raise RuntimeError(f"MPV player check failed: {str(e)}") from e

            return PlaybackService(
                upload_folder=upload_folder,
                db_session=db,
                socketio=socketio,
                logger=logger
            )
        except Exception as e:
            logger.error('PlaybackService initialization failed', {
                'error': str(e),
                'stack': True,
                'exception_type': type(e).__name__,
                'system': {
                    'python': sys.version,
                    'platform': platform.platform()
                }
            })
            return None
        
    @staticmethod
    def create_playlist_service(db, logger=None) -> Optional[PlaylistService]:
        """
        Создание сервиса работы с плейлистами
        :param db: Сессия базы данных
        :param logger: Логгер (опционально)
        :return: Экземпляр PlaylistService или None при ошибке
        """
        logger = logger or setup_logger('PlaylistService')
        try:
            logger.info('Initializing PlaylistService')
            return PlaylistService(db, logger)
        except Exception as e:
            logger.error('PlaylistService initialization failed', {
                'error': str(e),
                'stack': True
            })
            return None

    @staticmethod
    def create_settings_service(
        settings_file: str, 
        upload_folder: str, 
        logger=None
    ) -> Optional[SettingsService]:
        """
        Создание сервиса настроек
        :param settings_file: Путь к файлу настроек
        :param upload_folder: Папка для загрузки файлов
        :param logger: Логгер (опционально)
        :return: Экземпляр SettingsService или None при ошибке
        """
        logger = logger or setup_logger('SettingsService')
        try:
            logger.info('Initializing SettingsService', {
                'settings_file': settings_file
            })
            return SettingsService(settings_file, upload_folder, logger)
        except Exception as e:
            logger.error('SettingsService initialization failed', {
                'error': str(e),
                'stack': True
            })
            return None

    @staticmethod
    def create_auth_service(secret_key: str, logger=None) -> Optional[AuthService]:
        """
        Создание сервиса аутентификации
        :param secret_key: Секретный ключ для JWT
        :param logger: Логгер (опционально)
        :return: Экземпляр AuthService или None при ошибке
        """
        logger = logger or setup_logger('AuthService')
        try:
            logger.info('Initializing AuthService')
            return AuthService(secret_key, logger)
        except Exception as e:
            logger.error('AuthService initialization failed', {
                'error': str(e),
                'stack': True
            })
            return None

    @staticmethod
    def create_socket_service(socketio, db, logger=None) -> Optional[SocketService]:
        """
        Создание сервиса WebSocket
        :param socketio: Экземпляр SocketIO
        :param db: Сессия базы данных
        :param logger: Логгер (опционально)
        :return: Экземпляр SocketService или None при ошибке
        """
        logger = logger or setup_logger('SocketService')
        try:
            logger.info('Initializing SocketService')
            return SocketService(socketio, db, logger)
        except Exception as e:
            logger.error('SocketService initialization failed', {
                'error': str(e),
                'stack': True
            })
            return None

def init_services(
    config: Dict[str, Any], 
    db, 
    socketio=None, 
    logger=None
) -> Dict[str, Any]:
    """Инициализация всех сервисов приложения с расширенной диагностикой"""
    logger = logger or setup_logger('ServiceManager')
    
    try:
        logger.info('Starting services initialization with enhanced diagnostics')
        
        # 1. Проверка конфигурации
        required_config = {
            'UPLOAD_FOLDER': 'Путь к медиафайлам',
            'THUMBNAIL_FOLDER': 'Путь к миниатюрам', 
            'THUMBNAIL_URL': 'URL для миниатюр',
            'DEFAULT_LOGO': 'Файл логотипа по умолчанию',
            'SECRET_KEY': 'Секретный ключ'
        }
        
        missing_keys = [k for k in required_config if k not in config]
        if missing_keys:
            error_details = {k: required_config[k] for k in missing_keys}
            logger.critical('Missing required config', {'missing': error_details})
            raise ValueError(f"Отсутствуют обязательные параметры: {', '.join(missing_keys)}")

        # 2. Проверка файловой системы
        dir_checks = [
            (config['UPLOAD_FOLDER'], 'rw', 'Директория медиафайлов'),
            (config['THUMBNAIL_FOLDER'], 'rw', 'Директория миниатюр'),
            (os.path.dirname(config.get('SETTINGS_FILE', 'settings.json')), 'rw', 'Директория настроек')
        ]
        
        for path, mode, desc in dir_checks:
            try:
                path_obj = Path(path)
                if not path_obj.exists():
                    path_obj.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Создана директория: {path} ({desc})")
                
                # Проверка прав доступа
                if 'r' in mode and not os.access(path, os.R_OK):
                    raise PermissionError(f"Нет прав на чтение: {path}")
                if 'w' in mode and not os.access(path, os.W_OK):
                    raise PermissionError(f"Нет прав на запись: {path}")
                    
            except Exception as e:
                logger.critical(f"Ошибка доступа к {desc}", {
                    'path': path,
                    'error': str(e),
                    'required_access': mode
                })
                raise RuntimeError(f"Ошибка доступа к {desc.lower()}: {str(e)}")

        # 3. Проверка зависимостей системы
        try:
            # Проверка Pillow (для изображений)
            from PIL import Image
            Image.new('RGB', (1, 1)).save(io.BytesIO(), 'JPEG')
            
            # Проверка ffmpeg (для видео)
            ffmpeg_available = subprocess.run(
                ["ffmpeg", "-version"], 
                capture_output=True
            ).returncode == 0
            
            # Проверка MPV (для воспроизведения)
            mpv_available = subprocess.run(
                ["which", "mpv"],
                capture_output=True
            ).returncode == 0
            
            logger.info("Проверка зависимостей выполнена", {
                'Pillow': True,
                'ffmpeg': ffmpeg_available,
                'mpv': mpv_available
            })
            
            if not mpv_available:
                raise RuntimeError("MPV player not found in PATH")
                
        except Exception as e:
            logger.critical("Ошибка проверки зависимостей", {
                'error': str(e),
                'required': ['Pillow (для изображений)', 'ffmpeg (для видео)', 'mpv (для воспроизведения)']
            })
            raise RuntimeError(f"Системные зависимости не удовлетворены: {str(e)}")

        # 4. Инициализация ThumbnailService с расширенной диагностикой
        try:
            logger.info("Инициализация ThumbnailService...")
            
            # Дополнительные проверки перед созданием
            default_logo = Path(config['UPLOAD_FOLDER']) / config['DEFAULT_LOGO']
            if not default_logo.exists():
                logger.warning(f"Файл логотипа по умолчанию не найден: {default_logo}")
                try:
                    # Создаем временный логотип
                    from PIL import Image
                    Image.new('RGB', (100, 100), color='gray').save(default_logo)
                    logger.info(f"Создан временный логотип: {default_logo}")
                except Exception as e:
                    logger.error(f"Не удалось создать временный логотип: {str(e)}")

            thumbnail_service = ThumbnailService(
                upload_folder=config['UPLOAD_FOLDER'],
                thumbnail_folder=config['THUMBNAIL_FOLDER'],
                thumbnail_url=config['THUMBNAIL_URL'],
                default_thumbnail=config['DEFAULT_LOGO'],
                logger=logger
            )
            
            # Тестовая генерация миниатюры
            try:
                test_file = default_logo
                thumb_path = thumbnail_service.generate_thumbnail(test_file.name)
                if not thumb_path or not Path(thumb_path).exists():
                    raise RuntimeError("Тестовая генерация миниатюры не удалась")
                logger.info("ThumbnailService прошел тестовую генерацию")
            except Exception as e:
                logger.error("Тестовая генерация миниатюры провалилась", {
                    'test_file': str(test_file),
                    'error': str(e)
                })
                raise RuntimeError("ThumbnailService не прошел проверку работоспособности") from e
                
            logger.info("ThumbnailService успешно инициализирован")
            
        except Exception as e:
            logger.critical("Ошибка инициализации ThumbnailService", {
                'config': {
                    'upload_folder': config['UPLOAD_FOLDER'],
                    'thumbnail_folder': config['THUMBNAIL_FOLDER']
                },
                'error': str(e),
                'type': type(e).__name__,
                'stack': traceback.format_exc()
            })
            raise RuntimeError(f"Ошибка инициализации ThumbnailService: {str(e)}") from e

        # 5. Инициализация остальных сервисов
        services = {
            'thumbnail_service': thumbnail_service,
            'db': db
        }

        try:
            file_service = FileService(
                upload_folder=config['UPLOAD_FOLDER'],
                thumbnail_service=thumbnail_service,
                logger=logger
            )
            services['file_service'] = file_service
            logger.info("FileService успешно инициализирован")
        except Exception as e:
            logger.error("Ошибка инициализации FileService", {
                'error': str(e),
                'stack': traceback.format_exc()
            })
            raise RuntimeError(f"Ошибка инициализации FileService: {str(e)}") from e

        # Инициализация обязательных сервисов
        mandatory_services = [
            ('playback_service', lambda: ServiceFactory.create_playback_service(
                upload_folder=config['UPLOAD_FOLDER'],
                db=db,
                socketio=socketio,
                logger=logger
            ))
        ]

        for name, factory in mandatory_services:
            try:
                service = factory()
                if not service:
                    raise RuntimeError(f"Service {name} returned None")
                services[name] = service
                logger.info(f"{name} успешно инициализирован")
            except Exception as e:
                logger.critical(f"Ошибка инициализации обязательного сервиса {name}", {
                    'error': str(e),
                    'stack': traceback.format_exc(),
                    'system': {
                        'python': sys.version,
                        'platform': platform.platform()
                    }
                })
                raise RuntimeError(f"Ошибка инициализации обязательного сервиса {name}: {str(e)}") from e

        # Инициализация опциональных сервисов
        optional_services = [
            ('playlist_service', lambda: PlaylistService(db.session, logger)),
            ('settings_service', lambda: SettingsService(
                config.get('SETTINGS_FILE', 'settings.json'),
                config['UPLOAD_FOLDER'],
                logger
            )),
            ('auth_service', lambda: AuthService(config['SECRET_KEY'], logger))
        ]

        for name, factory in optional_services:
            try:
                service = factory()
                if service:
                    services[name] = service
                    logger.info(f"{name} успешно инициализирован")
                else:
                    logger.warning(f"{name} вернул None при инициализации")
            except Exception as e:
                logger.error(f"Ошибка инициализации {name}", {
                    'error': str(e),
                    'stack': traceback.format_exc()
                })

        # Инициализация SocketService если требуется
        if socketio:
            try:
                socket_service = SocketService(socketio, db, logger)
                services['socket_service'] = socket_service
                logger.info("SocketService успешно инициализирован")
            except Exception as e:
                logger.error("Ошибка инициализации SocketService", {
                    'error': str(e),
                    'stack': traceback.format_exc()
                })

        logger.info("Все сервисы инициализированы", {
            'initialized': list(services.keys()),
            'failed': [name for name, _ in optional_services if name not in services]
        })
        
        return services

    except Exception as e:
        logger.critical("Критическая ошибка инициализации сервисов", {
            'error': str(e),
            'type': type(e).__name__,
            'stack': traceback.format_exc(),
            'system': {
                'python': sys.version,
                'platform': platform.platform()
            }
        })
        raise RuntimeError(f"Ошибка инициализации сервисов: {str(e)}") from e

__all__ = [
    'ServiceFactory',
    'init_services',
    'FileService',
    'PlaybackService',
    'PlaylistService',
    'SettingsService',
    'AuthService',
    'SocketService',
    'ThumbnailService',
    'ServiceLogger'
]
