import os
from socket import gethostbyname, gethostname
from pathlib import Path

class Config:
    # Получаем текущий IP
    try:
        current_ip = gethostbyname(gethostname())
    except:
        current_ip = "127.0.0.1"  # Fallback IP
    
    # Обработка CORS origins
    extra_origins = os.getenv("EXTRA_CORS_ORIGINS", "").split(",") if os.getenv("EXTRA_CORS_ORIGINS") else []

    # Базовые пути
    BASE_DIR = Path(__file__).parent.parent
    
    # Пути к файлам
    DB_PATH = "/var/lib/dsign/database.db"
    SETTINGS_FILE = "/var/lib/dsign/settings.json"
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "/var/lib/dsign/media")
    BASE_DIR = Path(__file__).parent.parent
    STATIC_FOLDER = str(BASE_DIR / 'static')
    IDLE_LOGO = "idle_logo.jpg"
    DEFAULT_LOGO = 'idle_logo.jpg'
    DEFAULT_LOGO = IDLE_LOGO
    SCREENSHOT_DIR = '/var/lib/dsign/media'
    DEFAULT_LOGO_PATH = '/var/lib/dsign/media/idle_logo.jpg'
    THUMBNAIL_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbnails')
    THUMBNAIL_URL = '/media/thumbnails'  # URL-префикс для доступа к миниатюрам
    M3U_EXPORT_DIR = os.path.join(BASE_DIR, 'static/playlists')
    MEDIA_ROOT = '/var/lib/dsign/media'  # Физический путь к файлам
    MEDIA_URL = '/media/'  # URL-префикс для доступа к файлам
    
    # Создаем директории, если они не существуют
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    os.makedirs(STATIC_FOLDER, exist_ok=True)
    os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)
    os.makedirs(M3U_EXPORT_DIR, exist_ok=True)

    # Настройки приложения
    SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey123")
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB

    # Разрешенные расширения файлов
    ALLOWED_LOGO_EXTENSIONS = {'jpg', 'png', 'jpeg'}
    ALLOWED_LOGO_TYPES = ['image/jpeg', 'image/png']  # MIME-типы
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'mp4', 'avi'}
    
    # Настройки загрузки файлов
    MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2MB
    MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB для других изображений
    MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100MB для видео
    
    # Настройки сессии
    SESSION_COOKIE_NAME = 'dsign_session'
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400  # 1 day in seconds
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    
    # Настройки CORS
    CORS_SUPPORTS_CREDENTIALS = True
    SOCKETIO_CORS_ALLOWED_ORIGINS = "*"
    SOCKETIO_PING_TIMEOUT = 30
    SOCKETIO_PING_INTERVAL = 25
    SOCKETIO_ASYNC_MODE = 'gevent'

    # Режим отладки
    DEBUG = os.getenv("FLASK_ENV", "production").lower() == "development"

    # Настройки базы данных
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{DB_PATH}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Настройки процессов
    PROCESS_TIMEOUT = 5  # сек
    MPV_GRACE_PERIOD = 0.5  # сек

    # Конфигурация CORS
    CORS_ORIGINS = [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        f"http://{current_ip}:5000",
        *[x.strip() for x in extra_origins if x.strip()]
    ]

# Создаем экземпляр конфигурации
config = Config()

# Экспортируем часто используемые переменные для прямого импорта
UPLOAD_FOLDER = config.UPLOAD_FOLDER
STATIC_FOLDER = config.STATIC_FOLDER
IDLE_LOGO = config.IDLE_LOGO
SECRET_KEY = config.SECRET_KEY
DEBUG = config.DEBUG
SQLALCHEMY_DATABASE_URI = config.SQLALCHEMY_DATABASE_URI
ALLOWED_EXTENSIONS = config.ALLOWED_EXTENSIONS
ALLOWED_LOGO_EXTENSIONS = config.ALLOWED_LOGO_EXTENSIONS
MAX_CONTENT_LENGTH = config.MAX_CONTENT_LENGTH
CORS_SUPPORTS_CREDENTIALS = config.CORS_SUPPORTS_CREDENTIALS
THUMBNAIL_FOLDER = config.THUMBNAIL_FOLDER
THUMBNAIL_URL = config.THUMBNAIL_URL
M3U_EXPORT_DIR = config.M3U_EXPORT_DIR
MEDIA_ROOT = config.MEDIA_ROOT
MEDIA_URL = config.MEDIA_URL

__all__ = [
    'config',
    'Config',
    'UPLOAD_FOLDER',
    'STATIC_FOLDER',
    'IDLE_LOGO',
    'SECRET_KEY',
    'DEBUG',
    'SQLALCHEMY_DATABASE_URI',
    'ALLOWED_EXTENSIONS',
    'ALLOWED_LOGO_EXTENSIONS',
    'MAX_CONTENT_LENGTH',
    'CORS_SUPPORTS_CREDENTIALS',
    'BASE_DIR',
    'THUMBNAIL_FOLDER',
    'THUMBNAIL_URL',
    'M3U_EXPORT_DIR',
    'MEDIA_ROOT',
    'MEDIA_URL',
    'MAX_LOGO_SIZE',
    'MAX_IMAGE_SIZE',
    'MAX_VIDEO_SIZE',
    'ALLOWED_LOGO_TYPES',
]
