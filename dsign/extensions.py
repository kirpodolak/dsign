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

class LogoManager:
    def __init__(self, mpv_client, logger):
        self._mpv = mpv_client
        self.logger = logger
        self._current_state = 'stopped'
        
    def _update_playback_state(self):
        """Обновление внутреннего состояния воспроизведения"""
        try:
            pause_state = self._mpv.get_property('pause')
            self._current_state = 'paused' if pause_state else 'playing'
            return True
        except Exception as e:
            self.logger.warning(f"Could not update playback state: {str(e)}")
            return False

    def _execute_drm_command(self, command):
        """Выполнение DRM команды"""
        try:
            result = self._mpv.command(command)
            
            if result is None or result.get('error') == 'success':
                self._update_playback_state()
                self.logger.debug(f"DRM command succeeded: {command}")
                return True
            
            self.logger.warning(f"DRM command failed: {command} - {result.get('error', '')}")
            return False
            
        except Exception as e:
            self.logger.error(f"DRM command error: {command} - {str(e)}")
            return False

    def display_idle_logo(self, logo_path):
        """Отображение логотипа в режиме ожидания"""
        try:
            if not os.path.exists(logo_path):
                raise FileNotFoundError(f"Logo file not found: {logo_path}")

            commands = [
                ['stop'],
                ['loadfile', logo_path, 'replace'],
                ['set_property', 'loop-file', 'inf'],
                ['set_property', 'pause', 'no'],
                ['set_property', 'mute', 'yes'],
                ['set_property', 'video-aspect', '0'],
                ['set_property', 'video-zoom', '0'],
                ['set_property', 'video-pan-x', '0'],
                ['set_property', 'video-pan-y', '0']
            ]
            
            success = True
            for cmd in commands:
                if not self._execute_drm_command(cmd):
                    success = False
            
            if success:
                self.logger.info(f"Successfully displayed idle logo: {logo_path}")
            else:
                self.logger.warning("Some DRM commands failed during idle logo display")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Failed to display idle logo: {str(e)}")
            return False

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
        
        # 5. Инициализация сервисов
        with app.app_context():
            from .services import init_services  # Локальный импорт
            services = init_services(
                config=app.config,
                db=db,
                socketio=socketio,
                logger=logger
            )
        
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

__all__ = ['db', 'bcrypt', 'login_manager', 'socketio', 'init_extensions', 'LogoManager']
