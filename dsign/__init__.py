from flask import Flask
from flask_wtf import CSRFProtect
from typing import Dict, Any
import logging
import time
import subprocess
from pathlib import Path

from dsign.config.config import Config, config

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

        # Настройка уровня логирования
        app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)
        logger.info("Application instance created")

        # Инициализация расширений
        logger.info("Initializing extensions...")
        from .extensions import init_extensions, db
        services = init_extensions(app)
        csrf = CSRFProtect(app)
        logger.info("Extensions initialized successfully")

        # Инициализация сервисов
        logger.info("Initializing services...")
        from dsign.services.settings_service import SettingsService
        from dsign.services.playlist_service import PlaylistService
        from dsign.services.file_service import FileService
        from dsign.services.playback_service import PlaybackService
        
        # Создаем сессию БД для сервисов
        with app.app_context():
            db_session = db.session
            
            app.settings_service = SettingsService(
                settings_file=config_class.SETTINGS_FILE,
                upload_folder=config_class.UPLOAD_FOLDER,
                db_session=db_session
            )
            
            app.playlist_service = PlaylistService(db_session=db_session)
            app.file_service = FileService(upload_folder=config_class.UPLOAD_FOLDER)
            app.playback_service = PlaybackService(db_session=db.session)
            
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
        try:
            logger.info("Configuring playback service...")
            from .models import PlaybackStatus
            
            with app.app_context():
                # Проверка подключения к БД
                try:
                    db.session.query(PlaybackStatus).first()
                    logger.info("Database connection verified")
                except Exception as db_error:
                    logger.error(f"Database connection failed: {str(db_error)}")
                    raise RuntimeError("Database connection failed") from db_error

                playback_status = db.session.query(PlaybackStatus).first()
                if not playback_status or not playback_status.playlist_id:
                    logger.info("Starting idle logo...")
                    if services['playback_service'].display_idle_logo():
                        logger.info("Idle logo started successfully")
                    else:
                        logger.warning("Failed to start idle logo")

            # Запуск сервиса MPV
            try:
                subprocess.run(
                    ["sudo", "systemctl", "enable", "--now", "dsign-mpv.service"],
                    check=True
                )
                logger.info("MPV service enabled and started")
            except subprocess.CalledProcessError as e:
                logger.error(f"MPV service error: {str(e)}")
                raise RuntimeError("MPV service initialization failed") from e

        except Exception as e:
            logger.error(f"Playback configuration error: {str(e)}", exc_info=True)
            raise

        # Инициализация маршрутов
        try:
            logger.info("Initializing routes...")
            from .routes import init_routes
            init_routes(app, services)
            logger.info("Routes initialized successfully")
        except Exception as e:
            logger.error(f"Route initialization failed: {str(e)}", exc_info=True)
            raise RuntimeError(f"Route initialization error: {str(e)}") from e

        # Регистрация обработчиков ошибок
        register_error_handlers(app)
        logger.info("Error handlers registered")

        # Добавляем проверку маршрута /settings
        @app.route('/test-settings')
        def test_settings():
            try:
                with app.app_context():
                    from dsign.models import PlaybackProfile
                    profiles = db.session.query(PlaybackProfile).all()
                    return {
                        'status': 'success',
                        'profiles_count': len(profiles),
                        'services_initialized': all(
                            hasattr(app, service) for service in required_services
                        )
                    }
            except Exception as e:
                return {
                    'status': 'error',
                    'error': str(e)
                }, 500

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
