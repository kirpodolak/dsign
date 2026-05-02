from dsign import create_app
import logging
import os
import sys
from typing import NoReturn


def _journal_startup_banner() -> None:
    """One line to stderr → systemd journal; proves which interpreter/package tree ran."""
    root = os.environ.get("DSIGN_PROJECT_ROOT", "")
    pkg_file = ""
    try:
        import dsign as _dsign_pkg

        pkg_file = getattr(_dsign_pkg, "__file__", "") or ""
    except Exception as exc:
        pkg_file = f"<import failed: {exc}>"
    print(
        f"[dsign] pid={os.getpid()} exe={sys.executable} cwd={os.getcwd()} "
        f"DSIGN_PROJECT_ROOT={root!r} dsign_pkg={pkg_file}",
        file=sys.stderr,
        flush=True,
    )


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
    _journal_startup_banner()

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
            log_output=bool(app.config.get('DEBUG', False)),
            # Flask-SocketIO 5.x+ refuses embedded Werkzeug in "production" without this (systemd: debug=False).
            allow_unsafe_werkzeug=True,
        )
        
    except Exception as e:
        logging.critical(f"Failed to start server: {str(e)}", exc_info=True)
        raise RuntimeError("Server startup failed") from e

if __name__ == '__main__':
    run_server()
