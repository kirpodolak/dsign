from dsign import create_app
from dsign.services.logger import setup_logger
import logging
from typing import NoReturn
from dsign.extensions import socketio
import traceback

def configure_logging() -> None:
    """Настройка базового логгирования (только для startup)"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

def run_server() -> NoReturn:
    """Запуск сервера приложения"""
    # Базовая настройка логгирования для стартовых сообщений
    configure_logging()
    startup_logger = setup_logger('server.startup')
    
    try:
        startup_logger.info("Initializing Digital Signage Server")
        
        # Создание приложения
        app = create_app()
        
        # Получаем socketio из расширений
        from dsign.extensions import socketio
        
        startup_logger.info("Starting SocketIO server")
        
        socketio.run(
            app,
            host='0.0.0.0',
            port=5000,
            debug=app.config.get('DEBUG', False),
            use_reloader=False,
            log_output=app.config.get('SOCKETIO_LOGGER', False),
            allow_unsafe_werkzeug=app.debug  # Только для разработки
        )
        
    except Exception as e:
        startup_logger.critical(
            "Server startup failed", 
            extra={
                "error": str(e),
                "type": type(e).__name__,
                "traceback": traceback.format_exc()
            }
        )
        raise RuntimeError("Server startup failed") from e

if __name__ == '__main__':
    run_server()
