# Головной init
from flask import Flask
from flask_wtf import CSRFProtect
import logging
import time
import subprocess
from pathlib import Path
from threading import Thread

from dsign.config.config import Config, config
from dsign.services import init_services
from dsign.services.logger import ServiceLogger

def should_display_logo(db_session) -> bool:
    """Проверяет, нужно ли отображать логотип (нет активных плейлистов)"""
    from .models import PlaybackStatus
    status = db_session.query(PlaybackStatus).first()
    return not (status and status.playlist_id)

def check_mpv_service(logger, timeout=5, retries=3):
    """Проверяет статус MPV сервиса"""
    for attempt in range(retries):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "dsign-mpv.service"],
                check=True,
                timeout=timeout,
                capture_output=True,
                text=True
            )
            if result.stdout.strip() == "active":
                return True
            logger.warning(f"MPV service not active (attempt {attempt + 1}/{retries})")
        except subprocess.SubprocessError as e:
            logger.warning(f"MPV service check failed: {str(e)} (attempt {attempt + 1}/{retries})")
        time.sleep(1)
    return False

def create_app(config_class=config) -> Flask:
    """Фабрика для создания экземпляра Flask приложения"""
    # Инициализация приложения с ServiceLogger
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.static_folder = config_class.STATIC_FOLDER
    app.static_url_path = '/static'
    
    # Установка ServiceLogger как основного логгера
    app.logger = ServiceLogger('FlaskApp')
    
    try:
        app.logger.info("Starting application initialization")

        # 1. Проверка MPV сервиса
        app.logger.info("Checking MPV service status...")
        if not check_mpv_service(app.logger):
            app.logger.error("MPV service is not active")
            raise RuntimeError("MPV service is not running")
        app.logger.info("MPV service is active and ready")

        # 2. Инициализация расширений
        app.logger.info("Initializing extensions...")
        from .extensions import init_extensions, db, socketio
        init_extensions(app)
        csrf = CSRFProtect(app)
        app.logger.info("Extensions initialized successfully")

        # 3. Инициализация сервисов
        app.logger.info("Initializing services...")
        with app.app_context():
            services = init_services(
                config={
                    'UPLOAD_FOLDER': config_class.UPLOAD_FOLDER,
                    'SECRET_KEY': config_class.SECRET_KEY,
                    'SETTINGS_FILE': config_class.SETTINGS_FILE,
                    'THUMBNAIL_FOLDER': config_class.THUMBNAIL_FOLDER,
                    'THUMBNAIL_URL': config_class.THUMBNAIL_URL,
                    'DEFAULT_LOGO': config_class.DEFAULT_LOGO
                },
                db=db,
                socketio=socketio,
                logger=app.logger
            )
            
            # Прикрепляем сервисы к app
            for name, service in services.items():
                if name != 'db':
                    setattr(app, name, service)

        # Проверка обязательных сервисов
        required_services = [
            'playback_service',
            'settings_service', 
            'file_service',
            'playlist_service'
        ]
        
        for service in required_services:
            if not hasattr(app, service):
                app.logger.error(f"Required service not found: {service}")
                raise RuntimeError(f"Missing required service: {service}")
        app.logger.info("All required services verified")

        # Настройка сервиса воспроизведения
        app.logger.info("Configuring playback service...")
        from .models import PlaybackStatus
        
        with app.app_context():
            try:
                # Проверка подключения к БД
                playback_status = db.session.query(PlaybackStatus).first()
                app.logger.info("Database connection verified")
        
                # Определение состояния воспроизведения
                if not playback_status or not playback_status.playlist_id:
                    app.logger.info("No active playlist found, starting idle logo...")
            
                    def start_idle_logo():
                        max_attempts = 3
                        for attempt in range(max_attempts):
                            try:
                                if app.playback_service.display_idle_logo():
                                    return
                                app.logger.warning(f"Idle logo display failed (attempt {attempt + 1}/{max_attempts})")
                                time.sleep(2)
                            except Exception as e:
                                app.logger.error(f"Failed to display idle logo (attempt {attempt + 1}): {str(e)}")
                                time.sleep(2)
                        app.logger.error("All attempts to display idle logo failed")
            
                    # Запуск в фоновом потоке
                    Thread(target=start_idle_logo, daemon=True).start()
                else:
                    app.logger.info(f"Active playlist found (ID: {playback_status.playlist_id}), resuming playback...")
                    try:
                        if not app.playlist_service.play(playback_status.playlist_id):
                            app.logger.error("Failed to resume playlist playback, falling back to idle logo")
                            app.playback_service.display_idle_logo()
                    except Exception as e:
                        app.logger.error(f"Error resuming playback: {str(e)}")
                        app.logger.info("Falling back to idle logo due to playback error")
                        app.playback_service.display_idle_logo()
                
            except Exception as db_error:
                app.logger.error(f"Database/playback initialization failed: {str(db_error)}")
                app.logger.info("Attempting to start idle logo as fallback")
                try:
                    app.playback_service.display_idle_logo()
                except Exception as logo_error:
                    app.logger.critical(f"Complete initialization failure: {str(logo_error)}")
                raise RuntimeError("Initialization failed") from db_error

        # Инициализация маршрутов
        app.logger.info("Initializing routes...")
        from .routes import init_routes
        init_routes(app, services)
        app.logger.info("Routes initialized successfully")

        # Регистрация обработчиков ошибок
        register_error_handlers(app)
        app.logger.info("Error handlers registered")

        app.logger.info("Application initialization completed successfully")
        return app

    except Exception as e:
        app.logger.critical(f"Application initialization failed: {str(e)}")
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
