from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask import current_app
import os
import logging
from typing import Dict, Any
from dsign.config.config import Config, config

# Инициализация экземпляров расширений
db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
socketio = SocketIO()

def init_extensions(app) -> Dict[str, Any]:
    """
    Инициализация Flask-расширений без создания сервисов
    :param app: Экземпляр Flask приложения
    :return: Словарь с инициализированными расширениями
    """
    try:
        # 1. Инициализация базовых расширений
        db.init_app(app)
        bcrypt.init_app(app)
        
        # 2. Настройка SocketIO с более строгими параметрами
        socketio.init_app(
            app,
            async_mode=app.config['SOCKETIO_ASYNC_MODE'],
            cors_allowed_origins=app.config['SOCKETIO_CORS_ALLOWED_ORIGINS'],
            logger=app.config.get('SOCKETIO_LOGGER', True),
            engineio_logger=app.config.get('SOCKETIO_ENGINEIO_LOGGER', False),
            cors_credentials=app.config.get('SOCKETIO_CORS_CREDENTIALS', False),
            http_compression=app.config.get('SOCKETIO_HTTP_COMPRESSION', True),
            always_connect=True  # Важно для стабильности соединений
        )
        
        # 3. Настройка аутентификации
        _configure_auth(app)
        
        # 4. Создание рабочих директорий
        _ensure_directories(app)
        
        # 5. Установка обработчиков
        app.teardown_appcontext(_shutdown_session)
        
        # 6. Настройка кэширования
        configure_static_cache(app)
        
        app.logger.info("Extensions initialized successfully")
        return {
            'db': db,
            'bcrypt': bcrypt,
            'login_manager': login_manager,
            'socketio': socketio
        }
        
    except Exception as e:
        app.logger.critical(f"Extensions initialization failed: {str(e)}", exc_info=True)
        raise RuntimeError(f"Extensions initialization failed: {str(e)}")

def _configure_auth(app) -> None:
    """Настройка системы аутентификации"""
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.init_app(app)
    
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User  # Ленивый импорт
        try:
            return db.session.get(User, int(user_id))
        except Exception as e:
            app.logger.error(f"User loading error: {str(e)}")
            return None

def _ensure_directories(app) -> None:
    """Создание необходимых директорий"""
    required_dirs = [
        app.config['UPLOAD_FOLDER'],
        os.path.join(app.config['UPLOAD_FOLDER'], 'logo'),
        os.path.join(app.config['UPLOAD_FOLDER'], 'tmp'),
        app.config['THUMBNAIL_FOLDER']
    ]
    
    for directory in required_dirs:
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception as e:
            app.logger.error(f"Directory creation failed: {directory} - {str(e)}")
            raise

def _shutdown_session(exception=None) -> None:
    """Корректное завершение сессии БД"""
    if db.session:
        try:
            db.session.remove()
        except Exception as e:
            current_app.logger.error(f"Session shutdown error: {str(e)}")

def configure_static_cache(app):
    """Настройка кэширования статических файлов"""
    @app.after_request
    def add_cache_headers(response):
        if response.direct_passthrough:
            return response
            
        if response.status_code < 400 and any(
            response.headers.get('Content-Type', '').startswith(t) 
            for t in ['text/css', 'application/javascript', 'image/']
        ):
            response.cache_control.max_age = 86400
            response.cache_control.public = True
        return response

__all__ = ['db', 'bcrypt', 'login_manager', 'socketio', 'init_extensions']
