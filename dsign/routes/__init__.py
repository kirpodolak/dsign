# /dsign/routes/__init__.py
import time
import logging
from logging import Filter
from typing import Dict, Any, Tuple

from flask import Blueprint
from flask_login import current_user

class StaticFilter(Filter):
    """Фильтр для исключения статических запросов из логов"""
    def filter(self, record):
        msg = record.getMessage()
        static_paths = ['/favicon.ico', '/media/idle_logo.jpg']
        return not any(path in msg for path in static_paths)

def create_blueprints() -> Tuple[Blueprint, Blueprint]:
    """
    Создает и возвращает основные Blueprints приложения
    
    Returns:
        Tuple[Blueprint, Blueprint]: (main_bp, api_bp)
    """
    main_bp = Blueprint(
        'main', 
        __name__,
        template_folder='templates',
        static_folder='static',
        static_url_path='/static'
    )
    
    api_bp = Blueprint(
        'api',
        __name__,
        url_prefix='/api'
    )
    
    return main_bp, api_bp

def init_routes(app, services: Dict[str, Any]) -> None:
    """
    Инициализирует все маршруты приложения
    
    Args:
        app: Flask приложение
        services: Словарь с сервисами приложения
    """
    logger = logging.getLogger('dsign')
    logger.addFilter(StaticFilter())

    # Создание blueprint'ов
    main_bp, api_bp = create_blueprints()

    @main_bp.context_processor
    def inject_common_variables() -> Dict[str, Any]:
        """Инъекция общих переменных в контекст шаблонов"""
        timestamp = int(time.time())
        common_vars = {
            'current_user': current_user,
            'app_name': 'Digital Signage',
            'timestamp': timestamp,
            'logo_cache_buster': timestamp // 300,
            'default_logo_cache_buster': timestamp // 3600
        }

        try:
            logo_path = services['file_service'].get_logo_path()
            common_vars.update({
                'logo_url': f'/media/idle_logo.jpg?t={common_vars["logo_cache_buster"]}',
                'default_logo': False,
                'settings': services['settings_service'].get_settings()
            })
        except Exception as e:
            logger.debug(f"Using default logo: {str(e)}", exc_info=True)
            common_vars.update({
                'logo_url': f'/static/default-logo.png?t={common_vars["default_logo_cache_buster"]}',
                'default_logo': True
            })

        return common_vars

    # Ленивая загрузка маршрутов для избежания циклических импортов
    def _lazy_load_routes():
        from .auth_routes import auth_bp
        from .main_routes import init_main_routes
        from .api.api_routes import init_api_routes

        # Инициализация маршрутов
        init_main_routes(main_bp, services['settings_service'])
        init_api_routes(api_bp, services)

        # Регистрация blueprint'ов
        blueprints_to_register = [
            (auth_bp, '/api/auth'),
            (main_bp, None),
            (api_bp, None)
        ]

        for bp, url_prefix in blueprints_to_register:
            if bp.name not in app.blueprints:
                app.register_blueprint(bp, url_prefix=url_prefix)

    # Проверка обязательных сервисов
    required_services = ['file_service', 'playback_service', 'socket_service']
    for svc in required_services:
        if svc not in services:
            raise RuntimeError(f"Missing required service: {svc}")

    # Инициализация сокет-сервиса
    if 'socket_service' in services:
        socket_service = services['socket_service']
        try:
            if hasattr(socket_service, 'init_app'):
                socket_service.init_app(app)
                logger.info("Socket service initialized successfully")
            else:
                logger.warning("SocketService missing init_app method")
        except Exception as e:
            logger.error(f"Failed to initialize socket service: {str(e)}", exc_info=True)
            raise RuntimeError(f"Socket service initialization failed: {str(e)}")

    # Загрузка маршрутов
    _lazy_load_routes()

__all__ = ['create_blueprints', 'init_routes']
