# /dsign/routes/api/__init__.py
from flask import Blueprint
from .api_routes import init_api_routes
from .socket import socketio, init_socketio
from .logs import logs_bp, logger

def init_api(app):
    # Создаем основной Blueprint для API
    api_bp = Blueprint('api', __name__, url_prefix='/api')
    
    # Инициализируем WebSocket
    socketio_instance = init_socketio(app)
    
    # Регистрируем Blueprint для логов
    api_bp.register_blueprint(logs_bp)
    
    # Инициализируем основные API роуты
    init_api_routes(api_bp, services={
        'socketio': socketio_instance
    })
    
    # Регистрируем основной Blueprint
    app.register_blueprint(api_bp)
    
    return socketio_instance

__all__ = ['init_api', 'socketio', 'logger']
