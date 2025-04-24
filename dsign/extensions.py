from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_socketio import SocketIO
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
    # Настройка логгера
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    try:
        # 1. Инициализация Flask-расширений
        db.init_app(app)
        bcrypt.init_app(app)
        
        # Настройка SocketIO
        socketio.init_app(
            app,
            cors_allowed_origins=app.config.get('SOCKETIO_CORS_ALLOWED_ORIGINS', "*"),
            logger=logger,
            engineio_logger=logger if app.debug else False
        )
        
        # 2. Настройка аутентификации
        _configure_auth(app)
        
        # 3. Создание рабочих директорий
        _ensure_directories(app)
        
        # 4. Установка обработчиков
        app.teardown_appcontext(_shutdown_session)
        
        # 5. Инициализация сервисов с проверкой MPV
        with app.app_context():
            from .services import init_services  # Локальный импорт
            services = init_services(
                config=app.config,
                db=db,
                socketio=socketio,
                logger=logger
            )
            
            # Явная проверка инициализации MPV
            if 'playback_service' in services:
                if not hasattr(services['playback_service'], '_mpv_manager'):
                    logger.error("MPV manager not found in playback service")
                    raise RuntimeError("MPV manager initialization failed")
                
                if not services['playback_service']._mpv_manager.wait_for_mpv_ready(timeout=30):
                    logger.error("MPV initialization timeout")
                    raise RuntimeError("MPV failed to initialize")
                
                logger.info("MPV initialized and ready")
        
        logger.info("All extensions initialized successfully")
        return services
        
    except Exception as e:
        logger.critical(f"Failed to initialize extensions: {str(e)}", exc_info=True)
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
        app.logger.info(f"Created required directories in {upload_folder}")
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

__all__ = ['db', 'bcrypt', 'login_manager', 'socketio', 'init_extensions']
