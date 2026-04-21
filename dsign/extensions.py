from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask import request
import os
import logging
from typing import Dict, Any

# Инициализация экземпляров расширений
db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
socketio = SocketIO()

def init_extensions(app) -> Dict[str, Any]:
    """
    Полная инициализация всех компонентов
    :param app: Экземпляр Flask приложения
    :return: Словарь с инициализированными сервисами
    """
    try:
        # SocketIO/EngineIO can be very noisy; keep it quiet by default.
        logger = logging.getLogger("dsign.socketio")
        engineio_debug = bool(app.config.get("SOCKETIO_ENGINEIO_DEBUG", False))
        # Engine.IO close/transport diagnostics are logged at INFO/DEBUG.
        # When explicitly enabled, raise log level so journald actually captures them.
        logger.setLevel(
            logging.DEBUG
            if app.config.get("DEBUG", False) or engineio_debug
            else logging.WARNING
        )
        if engineio_debug:
            # Ensure Engine.IO logs reach stdout/stderr even when the app uses a custom logger.
            # systemd captures stdout/stderr into journald.
            logger.propagate = True
            if not logger.handlers:
                h = logging.StreamHandler()
                h.setLevel(logging.DEBUG)
                h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
                logger.addHandler(h)

        # 1. Инициализация Flask-расширений
        db.init_app(app)
        bcrypt.init_app(app)
        
        # Настройка SocketIO
        socketio.init_app(
            app,
            cors_allowed_origins=app.config.get('SOCKETIO_CORS_ALLOWED_ORIGINS', "*"),
            async_mode=app.config.get('SOCKETIO_ASYNC_MODE', 'eventlet'),
            ping_interval=app.config.get('SOCKETIO_PING_INTERVAL', 25),
            ping_timeout=app.config.get('SOCKETIO_PING_TIMEOUT', 60),
            # Only enable Socket.IO internal logs when explicitly debugging.
            logger=logger if engineio_debug else False,
            engineio_logger=logger if engineio_debug else False
        )
        
        # 2. Настройка аутентификации
        _configure_auth(app)
        
        # 3. Создание рабочих директорий
        _ensure_directories(app)
        
        # 4. Установка обработчиков
        app.teardown_appcontext(_shutdown_session)
        
        # 5. Настройка кэширования статических файлов
        configure_static_cache(app)
        return {}
        
    except Exception as e:
        logging.getLogger(__name__).critical(
            f"Failed to initialize extensions: {str(e)}", exc_info=True
        )
        raise RuntimeError(f"Extensions initialization failed: {str(e)}")

def _configure_auth(app) -> None:
    """Настройка системы аутентификации"""
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.init_app(app)
    
    # Импорт модели User только внутри функции
    from .models import User
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except Exception as e:
            app.logger.error(f"Error loading user {user_id}: {str(e)}")
            return None

def _ensure_directories(app) -> None:
    """Создание необходимых директорий"""
    upload_folder = app.config.get('UPLOAD_FOLDER', 'uploads')
    try:
        os.makedirs(upload_folder, exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'logo'), exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'tmp'), exist_ok=True)
        app.logger.debug(f"Created required directories in {upload_folder}")
    except Exception as e:
        app.logger.error(f"Failed to create directories: {str(e)}")
        raise RuntimeError(f"Directory creation failed: {str(e)}")

def _shutdown_session(exception=None) -> None:
    """Корректное завершение сессии БД"""
    try:
        if db.session:
            db.session.remove()
    except Exception as e:
        logging.getLogger(__name__).error(f"Error during session shutdown: {str(e)}")
        
def configure_static_cache(app):
    """Настройка кэширования статических файлов"""
    @app.after_request
    def add_cache_headers(response):
        if request.path.startswith('/static/'):
            response.cache_control.max_age = 86400  # 1 день
            response.cache_control.public = True
        return response

__all__ = ['db', 'bcrypt', 'login_manager', 'socketio', 'init_extensions', 'configure_static_cache']
