import os
import socket
import netifaces
from pathlib import Path
from urllib.parse import urlparse

class Config:
    def __init__(self):
        # Получаем текущий IP и все сетевые IP
        self.current_ip = self._get_local_ip()
        self.all_network_ips = self._get_all_network_ips()
        self.port = 5000  # Порт приложения
        
        # Базовые пути
        self.BASE_DIR = Path(__file__).parent.parent
        
        # Пути к файлам
        self.DB_PATH = "/var/lib/dsign/database.db"
        self.SETTINGS_FILE = "/var/lib/dsign/settings.json"
        self.UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "/var/lib/dsign/media")
        self.STATIC_FOLDER = str(self.BASE_DIR / 'static')
        self.IDLE_LOGO = "idle_logo.jpg"
        self.DEFAULT_LOGO = self.IDLE_LOGO
        self.SCREENSHOT_DIR = '/var/lib/dsign/media'
        self.DEFAULT_LOGO_PATH = '/var/lib/dsign/media/idle_logo.jpg'
        self.THUMBNAIL_FOLDER = os.path.join(self.UPLOAD_FOLDER, 'thumbnails')
        self.THUMBNAIL_URL = '/media/thumbnails'
        self.M3U_EXPORT_DIR = os.path.join(self.BASE_DIR, 'static/playlists')
        self.MEDIA_ROOT = '/var/lib/dsign/media'
        self.MEDIA_URL = '/media/'
        
        # Создаем директории
        os.makedirs(self.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(os.path.dirname(self.DB_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(self.SETTINGS_FILE), exist_ok=True)
        os.makedirs(self.STATIC_FOLDER, exist_ok=True)
        os.makedirs(self.THUMBNAIL_FOLDER, exist_ok=True)
        os.makedirs(self.M3U_EXPORT_DIR, exist_ok=True)

        # Настройки приложения
        self.SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey123")
        self.MAX_CONTENT_LENGTH = 100 * 1024 * 1024
        self.ALLOWED_LOGO_EXTENSIONS = {'jpg', 'png', 'jpeg'}
        self.ALLOWED_LOGO_TYPES = ['image/jpeg', 'image/png']
        self.ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'mp4', 'avi'}
        self.MAX_LOGO_SIZE = 2 * 1024 * 1024
        self.MAX_IMAGE_SIZE = 5 * 1024 * 1024
        self.MAX_VIDEO_SIZE = 100 * 1024 * 1024
        self.MEDIA_STREAM_BUFFER_SIZE = 1024 * 1024  # 1MB буферизация
        self.MEDIA_MAX_CONCURRENT_STREAMS = 3  # Лимит одновременных потоков
        
        # Настройки сессии
        self.SESSION_COOKIE_NAME = 'dsign_session'
        self.SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() == "true"
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = 'Lax'
        self.PERMANENT_SESSION_LIFETIME = 86400
        self.REMEMBER_COOKIE_HTTPONLY = True
        self.REMEMBER_COOKIE_SECURE = self.SESSION_COOKIE_SECURE
        self.REMEMBER_COOKIE_SAMESITE = 'Lax'
        
        # Настройки CORS
        self.CORS_SUPPORTS_CREDENTIALS = True
        self.DEBUG = os.getenv("FLASK_ENV", "production").lower() == "development"

        # Настройки CORS origins
        self.CORS_ORIGINS = ["*"] if self.DEBUG else self._get_default_origins()
        self.SOCKETIO_CORS_ALLOWED_ORIGINS = "*"
        
        # Настройки Socket.IO
        self.SOCKETIO_CORS_METHODS = ["GET", "POST", "OPTIONS"]
        self.SOCKETIO_CORS_HEADERS = ["Content-Type", "Authorization"]
        self.SOCKETIO_MAX_HTTP_BUFFER_SIZE = 100 * 1024 * 1024
        self.SOCKETIO_PING_TIMEOUT = 300
        self.SOCKETIO_PING_INTERVAL = 60
        self.SOCKETIO_ASYNC_MODE = 'eventlet'
        self.SOCKETIO_LOGGER = True
        self.SOCKETIO_ENGINEIO_LOGGER = True
        self.SOCKETIO_ALWAYS_CONNECT = True
        self.SOCKETIO_CORS_CREDENTIALS = True
        self.SOCKETIO_HTTP_COMPRESSION = True
        self.SOCKETIO_MESSAGE_QUEUE = 'redis://localhost:6379/0'

        # Настройки Socket_Token
        self.SOCKET_TOKEN_SECRET = os.getenv("SECRET_KEY", "supersecretkey123")
        self.SOCKET_TOKEN_ISSUER = 'media-server'
        self.SOCKET_TOKEN_AUDIENCE = 'socket-client'
        self.SOCKET_TOKEN_EXPIRE_MINUTES = 30

        # Настройки сессии Socket.IO
        self.SOCKETIO_COOKIE = 'dsign_socketio'
        self.SOCKETIO_SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() == "true"
        self.SOCKETIO_SESSION_COOKIE_SAMESITE = 'Lax'

        # Настройки базы данных
        self.SQLALCHEMY_DATABASE_URI = f'sqlite:///{self.DB_PATH}'
        self.SQLALCHEMY_TRACK_MODIFICATIONS = False

        # Настройки процессов
        self.PROCESS_TIMEOUT = 5
        self.MPV_GRACE_PERIOD = 0.5

    def _get_local_ip(self):
        """Автоматически определяет локальный IP"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except:
            return "127.0.0.1"
    
    def _get_all_network_ips(self):
        """Получает все IP-адреса сетевых интерфейсов"""
        ips = set()
        try:
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        ip = addr_info.get('addr')
                        if ip and ip != '127.0.0.1':
                            ips.add(ip)
        except Exception as e:
            print(f"Error getting network interfaces: {e}")
        return list(ips) if ips else [self._get_local_ip()]

    def _get_default_origins(self, ws=False):
        """Генерирует допустимые origins на основе всех сетевых IP"""
        protocol = "ws" if ws else "http"
        base_origins = [
            f"{protocol}://localhost:{self.port}",
            f"{protocol}://127.0.0.1:{self.port}",
            f"{protocol}://0.0.0.0:{self.port}",
            "*",  # Разрешить все origins (менее безопасно)
            r"http://\d+\.\d+\.\d+\.\d+(:\d+)?",
            r"https://\d+\.\d+\.\d+\.\d+(:\d+)?"
        ]
        
        # Добавляем все обнаруженные IP-адреса
        for ip in set([self.current_ip] + self.all_network_ips):
            base_origins.append(f"{protocol}://{ip}:{self.port}")
        
        # Добавляем origins из переменных окружения
        extra_origins = os.getenv("EXTRA_CORS_ORIGINS", "").split(",")
        base_origins.extend([f"{protocol}://{x.strip()}:{self.port}" for x in extra_origins if x.strip()])
        
        return list(set(base_origins))  # Удаляем дубликаты

config = Config()

# Экспортируемые переменные
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
SOCKETIO_CORS_ALLOWED_ORIGINS = config.SOCKETIO_CORS_ALLOWED_ORIGINS
CORS_ORIGINS = config.CORS_ORIGINS
SOCKETIO_ASYNC_MODE = config.SOCKETIO_ASYNC_MODE
BASE_DIR = config.BASE_DIR
MAX_LOGO_SIZE = config.MAX_LOGO_SIZE
MAX_IMAGE_SIZE = config.MAX_IMAGE_SIZE
MAX_VIDEO_SIZE = config.MAX_VIDEO_SIZE
ALLOWED_LOGO_TYPES = config.ALLOWED_LOGO_TYPES

__all__ = [
    'config', 'Config', 'UPLOAD_FOLDER', 'STATIC_FOLDER', 'IDLE_LOGO',
    'SECRET_KEY', 'DEBUG', 'SQLALCHEMY_DATABASE_URI', 'ALLOWED_EXTENSIONS',
    'ALLOWED_LOGO_EXTENSIONS', 'MAX_CONTENT_LENGTH', 'CORS_SUPPORTS_CREDENTIALS',
    'THUMBNAIL_FOLDER', 'THUMBNAIL_URL', 'M3U_EXPORT_DIR', 'MEDIA_ROOT',
    'MEDIA_URL', 'SOCKETIO_CORS_ALLOWED_ORIGINS', 'CORS_ORIGINS',
    'SOCKETIO_ASYNC_MODE', 'BASE_DIR', 'MAX_LOGO_SIZE', 'MAX_IMAGE_SIZE',
    'MAX_VIDEO_SIZE', 'ALLOWED_LOGO_TYPES'
]
