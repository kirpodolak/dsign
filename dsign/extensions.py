from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect
from flask import request, jsonify, redirect, url_for
import os
import logging
from typing import Dict, Any, Optional

# Инициализация экземпляров расширений
db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
socketio = SocketIO()
csrf = CSRFProtect()


def _is_file_sqlite_uri(uri: Optional[str]) -> bool:
    if not uri or not str(uri).startswith("sqlite"):
        return False
    u = str(uri).lower()
    return ":memory:" not in u and "mode=memory" not in u


def configure_sqlite_engine_options(app) -> None:
    """
    Use NullPool for on-disk SQLite so background threads cannot exhaust QueuePool
    while holding a checked-out connection during long playback IPC.
    Leave :memory: alone (pytest relies on StaticPool / shared connection).
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not _is_file_sqlite_uri(uri):
        return
    from sqlalchemy.pool import NullPool

    opts = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
    if opts.get("poolclass") is None:
        opts["poolclass"] = NullPool
    connect_args = dict(opts.get("connect_args") or {})
    connect_args.setdefault("check_same_thread", False)
    # Wait for SQLite locks instead of failing immediately under concurrent writers.
    connect_args.setdefault("timeout", 30)
    opts["connect_args"] = connect_args
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts


def init_extensions(app) -> Dict[str, Any]:
    """
    Полная инициализация всех компонентов
    :param app: Экземпляр Flask приложения
    :return: Словарь с инициализированными сервисами
    """
    try:
        # SocketIO/EngineIO can be very noisy; keep it quiet by default.
        logger = logging.getLogger("dsign.socketio")
        engineio_debug = bool(app.config.get("SOCKETIO_ENGINEIO_DEBUG", False))
        # Engine.IO close/transport diagnostics are logged at INFO/DEBUG.
        # When explicitly enabled, raise log level so journald actually captures them.
        logger.setLevel(
            logging.DEBUG
            if app.config.get("DEBUG", False) or engineio_debug
            else logging.WARNING
        )
        if engineio_debug:
            # Ensure Engine.IO logs reach stdout/stderr even when the app uses a custom logger.
            # systemd captures stdout/stderr into journald.
            logger.propagate = True
            if not logger.handlers:
                h = logging.StreamHandler()
                h.setLevel(logging.DEBUG)
                h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
                logger.addHandler(h)
            # Also enable upstream loggers used by python-socketio / python-engineio.
            # These are the ones that typically log close reasons and transport errors.
            for name in ("engineio", "socketio"):
                l = logging.getLogger(name)
                l.setLevel(logging.DEBUG)
                l.propagate = True
                if not l.handlers:
                    l.addHandler(h)

        # 1. Инициализация Flask-расширений
        configure_sqlite_engine_options(app)
        db.init_app(app)
        # Register models and create missing tables (SQLite; additive schema, no Alembic in-tree).
        import dsign.models as _dsign_models  # noqa: F401

        with app.app_context():
            try:
                db.create_all()
                _ensure_playlist_sort_order_column(app)
                _ensure_schedule_schema(app)
            except Exception as e:
                app.logger.error(f"db.create_all() failed: {e}", exc_info=True)
                raise
        bcrypt.init_app(app)
        
        # Настройка SocketIO
        socketio.init_app(
            app,
            cors_allowed_origins=app.config.get('SOCKETIO_CORS_ALLOWED_ORIGINS', "*"),
            async_mode=app.config.get('SOCKETIO_ASYNC_MODE', 'threading'),
            ping_interval=app.config.get('SOCKETIO_PING_INTERVAL', 25),
            ping_timeout=app.config.get('SOCKETIO_PING_TIMEOUT', 60),
            # Only enable Socket.IO internal logs when explicitly debugging.
            logger=logger if engineio_debug else False,
            engineio_logger=logger if engineio_debug else False
        )
        
        # 2. Настройка аутентификации
        _configure_auth(app)
        
        # 3. Создание рабочих директорий
        _ensure_directories(app)
        
        # 4. Установка обработчиков
        app.teardown_appcontext(_shutdown_session)
        
        # 5. Настройка кэширования статических файлов
        configure_static_cache(app)
        return {}
        
    except Exception as e:
        logging.getLogger(__name__).critical(
            f"Failed to initialize extensions: {str(e)}", exc_info=True
        )
        raise RuntimeError(f"Extensions initialization failed: {str(e)}")

def _configure_auth(app) -> None:
    """Настройка системы аутентификации"""
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def _handle_unauthorized():
        """API: JSON 401 вместо редиректа на HTML-логин (избегает 302→GET login→405 у fetch)."""
        path = request.path or ''
        if path.startswith('/api/'):
            return jsonify({
                'success': False,
                'error': 'Authentication required',
                'authenticated': False,
            }), 401
        return redirect(url_for(login_manager.login_view, next=request.url))

    # Импорт модели User только внутри функции
    from .models import User
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except Exception as e:
            app.logger.error(f"Error loading user {user_id}: {str(e)}")
            return None

def _ensure_playlist_sort_order_column(app) -> None:
    """SQLite: add playlists.sort_order if missing (home page drag-and-drop order)."""
    from sqlalchemy import inspect, text
    from .models import Playlist

    try:
        insp = inspect(db.engine)
        if 'playlists' not in insp.get_table_names():
            return
        cols = {c['name'] for c in insp.get_columns('playlists')}
        if 'sort_order' not in cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE playlists ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0'))
            app.logger.info('Added playlists.sort_order column')
            rows = Playlist.query.order_by(Playlist.last_modified.desc(), Playlist.id.asc()).all()
            for idx, row in enumerate(rows):
                row.sort_order = idx
            db.session.commit()
    except Exception as e:
        app.logger.warning(f'playlist sort_order migration skipped: {e}')
        db.session.rollback()


def _ensure_schedule_schema(app) -> None:
    """SQLite: schedule_rules table + playback_status columns."""
    from sqlalchemy import inspect, text
    from .models import ScheduleRule, ScheduleException  # noqa: F401

    try:
        insp = inspect(db.engine)
        tables = insp.get_table_names()
        if 'schedule_rules' not in tables:
            ScheduleRule.__table__.create(db.engine)
            app.logger.info('Created schedule_rules table')
        if 'schedule_exceptions' not in tables:
            ScheduleException.__table__.create(db.engine)
            app.logger.info('Created schedule_exceptions table')

        if 'playback_status' not in tables:
            return
        cols = {c['name'] for c in insp.get_columns('playback_status')}
        for col_name, col_type in [
            ('source', 'VARCHAR(16)'),
            ('rule_id', 'INTEGER'),
            ('previous_source', 'VARCHAR(16)'),
            ('previous_rule_id', 'INTEGER'),
            ('previous_playlist_id', 'INTEGER'),
        ]:
            if col_name not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE playback_status ADD COLUMN {col_name} {col_type}'
                    ))
                app.logger.info(f'Added playback_status.{col_name}')
    except Exception as e:
        app.logger.warning(f'schedule schema migration skipped: {e}')
        db.session.rollback()


def _ensure_directories(app) -> None:
    """Создание необходимых директорий"""
    upload_folder = app.config.get('UPLOAD_FOLDER', 'uploads')
    try:
        os.makedirs(upload_folder, exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'logo'), exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'tmp'), exist_ok=True)
        app.logger.debug(f"Created required directories in {upload_folder}")
    except Exception as e:
        app.logger.error(f"Failed to create directories: {str(e)}")
        raise RuntimeError(f"Directory creation failed: {str(e)}")

def _shutdown_session(exception=None) -> None:
    """Корректное завершение сессии БД"""
    try:
        if db.session:
            db.session.remove()
    except Exception as e:
        logging.getLogger(__name__).error(f"Error during session shutdown: {str(e)}")
        
def configure_static_cache(app):
    """Настройка кэширования статических файлов"""
    @app.after_request
    def add_cache_headers(response):
        if request.path.startswith('/static/'):
            response.cache_control.max_age = 86400  # 1 день
            response.cache_control.public = True
        return response

__all__ = [
    'db',
    'bcrypt',
    'login_manager',
    'socketio',
    'csrf',
    'init_extensions',
    'configure_static_cache',
    'configure_sqlite_engine_options',
]
