from flask import Blueprint
from .api_routes import init_api_routes
from .logs import logs_bp, logger
from dsign.extensions import socketio  # Импорт из общего модуля

def init_api(app):
    # Создаем основной Blueprint для API
    api_bp = Blueprint('api', __name__, url_prefix='/api')
    
    # Регистрируем Blueprint для логов
    api_bp.register_blueprint(logs_bp)
    
    # Инициализируем основные API роуты
    init_api_routes(api_bp, services={
        'socketio': socketio  # Используем socketio из extensions
    })
    
    # Регистрируем основной Blueprint
    app.register_blueprint(api_bp)
    
    return socketio  # Возвращаем экземпляр из extensions

__all__ = ['init_api', 'logger']  # Убрали socketio из __all__
