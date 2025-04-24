from flask import Flask
from flask_wtf import CSRFProtect
import logging
import time
import subprocess
from pathlib import Path
from threading import Thread

from dsign.config.config import Config, config

def should_display_logo(db_session) -> bool:
    """Проверяет, нужно ли отображать логотип (нет активных плейлистов)"""
    from .models import PlaybackStatus
    status = db_session.query(PlaybackStatus).first()
    return not (status and status.playlist_id)

def create_app(config_class=config) -> Flask:
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)

    try:
        app = Flask(__name__)
        app.config.from_object(config_class)

        app.static_folder = config_class.STATIC_FOLDER
        app.static_url_path = '/static'
        app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)
        logger.info("Application instance created")

        # 1. Запуск MPV сервиса
        logger.info("Starting MPV service...")
        try:
            subprocess.run(
                ["sudo", "systemctl", "enable", "--now", "dsign-mpv.service"],
                check=True,
                timeout=30
            )
            logger.info("MPV service enabled and started")
            time.sleep(3)  # Даем время для инициализации
        except Exception as e:
            logger.error(f"MPV service error: {str(e)}")
            raise RuntimeError("MPV service initialization failed") from e

        # 2. Инициализация расширений
        logger.info("Initializing extensions...")
        from .extensions import init_extensions, db, socketio
        services = init_extensions(app)
        csrf = CSRFProtect(app)
        logger.info("Extensions initialized successfully")

        # 3. Инициализация сервисов
        logger.info("Initializing services...")
        from dsign.services.settings_service import SettingsService
        from dsign.services.playlist_service import PlaylistService
        from dsign.services.file_service import FileService
        from dsign.services.playback_service import PlaybackService
        
        with app.app_context():
            # Инициализация сервисов
            app.settings_service = SettingsService(
                settings_file=config_class.SETTINGS_FILE,
                upload_folder=config_class.UPLOAD_FOLDER
            )
            
            app.file_service = FileService(upload_folder=config_class.UPLOAD_FOLDER)
            app.playlist_service = PlaylistService(db.session)
            
            # Инициализация PlaybackService
            app.playback_service = PlaybackService(
                upload_folder=config_class.UPLOAD_FOLDER,
                db_session=db.session,
                socketio=socketio,
                logger=logger
            )
            
        logger.info("Services initialized successfully")

        # Проверка обязательных сервисов
        required_services = [
            'playback_service',
            'settings_service', 
            'file_service',
            'playlist_service'
        ]
        
        for service in required_services:
            if not hasattr(app, service):
                logger.error(f"Required service not found: {service}")
                raise RuntimeError(f"Missing required service: {service}")
        logger.info("All required services verified")

        # Настройка сервиса воспроизведения
        logger.info("Configuring playback service...")
        from .models import PlaybackStatus
        
        with app.app_context():
            try:
                # Проверка подключения к БД и получение статуса воспроизведения
                playback_status = db.session.query(PlaybackStatus).first()
                logger.info("Database connection verified")
        
                # Определение состояния воспроизведения
                if not playback_status or not playback_status.playlist_id:
                    logger.info("No active playlist found, starting idle logo...")
            
                    def start_idle_logo():
                        try:
                            if not app.playback_service.display_idle_logo():
                                logger.warning("Initial idle logo display failed, retrying...")
                                time.sleep(2)  # Задержка перед повторной попыткой
                                app.playback_service.display_idle_logo()
                        except Exception as e:
                            logger.error(f"Failed to display idle logo: {str(e)}")
            
                    # Запуск в фоновом потоке
                    Thread(target=start_idle_logo, daemon=True).start()
                else:
                    logger.info(f"Active playlist found (ID: {playback_status.playlist_id}), resuming playback...")
                    try:
                        if not app.playlist_service.play(playback_status.playlist_id):
                            logger.error("Failed to resume playlist playback, falling back to idle logo")
                            app.playback_service.display_idle_logo()
                    except Exception as e:
                        logger.error(f"Error resuming playback: {str(e)}")
                        logger.info("Falling back to idle logo due to playback error")
                        app.playback_service.display_idle_logo()
                
            except Exception as db_error:
                logger.error(f"Database/playback initialization failed: {str(db_error)}")
                logger.info("Attempting to start idle logo as fallback")
                try:
                    app.playback_service.display_idle_logo()
                except Exception as logo_error:
                    logger.critical(f"Complete initialization failure: {str(logo_error)}")
                raise RuntimeError("Initialization failed") from db_error

        # Инициализация маршрутов
        logger.info("Initializing routes...")
        from .routes import init_routes
        init_routes(app, services)
        logger.info("Routes initialized successfully")

        # Регистрация обработчиков ошибок
        register_error_handlers(app)
        logger.info("Error handlers registered")

        logger.info("Application initialization completed successfully")
        return app

    except Exception as e:
        logger.critical(f"Application initialization failed: {str(e)}", exc_info=True)
        raise RuntimeError(f"Application startup failed: {str(e)}") from e

def register_error_handlers(app: Flask) -> None:
    from flask import jsonify, render_template, request

    def is_api_request():
        return request.path.startswith(('/api/', '/auth/'))

    @app.errorhandler(400)
    def bad_request_error(error):
        app.logger.warning(f"Bad request: {str(error)}")
        if is_api_request():
            return jsonify({
                "success": False,
                "error": "Bad Request",
                "message": str(error.description) if hasattr(error, 'description') else "Invalid request"
            }), 400
        return render_template('errors/400.html'), 400

    @app.errorhandler(401)
    def unauthorized_error(error):
        app.logger.warning(f"Unauthorized: {str(error)}")
        if is_api_request():
            return jsonify({
                "success": False,
                "error": "Unauthorized",
                "message": "Authentication required"
            }), 401
        return render_template('errors/401.html'), 401

    @app.errorhandler(403)
    def forbidden_error(error):
        app.logger.warning(f"Forbidden: {str(error)}")
        if is_api_request():
            return jsonify({
                "success": False,
                "error": "Forbidden",
                "message": "Insufficient permissions"
            }), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found_error(error):
        app.logger.warning(f"Not Found: {str(error)}")
        if is_api_request():
            return jsonify({
                "success": False,
                "error": "Not Found",
                "message": str(error.description) if hasattr(error, 'description') else "Resource not found"
            }), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.error(f"Server Error: {str(error)}", exc_info=True)
        if is_api_request():
            return jsonify({
                "success": False,
                "error": "Internal Server Error", 
                "message": "An unexpected error occurred"
            }), 500
        return render_template('errors/500.html'), 500
