from dsign import create_app
import logging
from typing import NoReturn

def configure_logging() -> None:
    """
    Keep logging configuration minimal here.
    The application uses `dsign.services.logger.ServiceLogger` for structured output.
    Calling basicConfig() with handlers causes duplicate log lines.
    """
    logging.getLogger().setLevel(logging.INFO)

def run_server() -> NoReturn:
    """Запуск сервера приложения"""
    # Настройка логгирования
    configure_logging()
    
    try:
        # Создание приложения
        app = create_app()
        app.logger.info("Starting Digital Signage Server")
        
        # Настройка SocketIO
        from dsign.extensions import socketio
        
        socketio.run(
            app,
            host='0.0.0.0',
            port=5000,
            debug=app.config.get('DEBUG', False),
            use_reloader=False,
            # Avoid EngineIO "emitting event ..." spam in production.
            log_output=bool(app.config.get('DEBUG', False))
            # Убрано: allow_unsafe_werkzeug=True
        )
        
    except Exception as e:
        logging.critical(f"Failed to start server: {str(e)}", exc_info=True)
        raise RuntimeError("Server startup failed") from e

if __name__ == '__main__':
    run_server()
