# /dsign/routes/__init__.py
import time
import logging
from logging import Filter
from typing import Dict, Any

from flask import Blueprint
from flask_login import current_user

class StaticFilter(Filter):
    def filter(self, record):
        msg = record.getMessage()
        static_paths = ['/favicon.ico', '/media/idle_logo.jpg']
        return not any(path in msg for path in static_paths)

def create_blueprints():
    main_bp = Blueprint('main', __name__, template_folder='templates', 
                       static_folder='static', static_url_path='/static')
    api_bp = Blueprint('api', __name__, url_prefix='/api')
    return main_bp, api_bp

def init_routes(app, services: Dict[str, Any]):
    logger = logging.getLogger('dsign')
    logger.addFilter(StaticFilter())

    main_bp, api_bp = create_blueprints()

    @main_bp.context_processor
    def inject_common_variables() -> Dict[str, Any]:
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
            common_vars.update({
                'logo_url': f'/static/default-logo.png?t={common_vars["default_logo_cache_buster"]}',
                'default_logo': True
            })
            logger.debug(f"Using default logo: {str(e)}")

        return common_vars

    # Импорт маршрутов - изменено на прямой импорт auth_bp
    from .auth_routes import auth_bp
    from .main_routes import init_main_routes
    from .api.api_routes import init_api_routes

    # Инициализация маршрутов
    init_main_routes(main_bp, services['settings_service'])
    init_api_routes(api_bp, services)

    # Проверка сервисов
    required_services = ['file_service', 'playback_service', 'socket_service']
    for svc in required_services:
        if svc not in services:
            raise RuntimeError(f"Missing required service: {svc}")

    # Регистрация blueprint'ов
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    # Инициализация сокетов
    if 'socket_service' in services:
        services['socket_service'].init_app(app)

__all__ = ['create_blueprints', 'init_routes']
