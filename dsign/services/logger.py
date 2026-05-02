"""
/dsign/service/logger.py
Модуль централизованного логирования сервисов с поддержкой Flask
"""

import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, Union
import json
from pathlib import Path
from datetime import datetime  # <-- Восстановленный импорт
from flask import Flask
import sys
import os
import traceback

class ServiceLogger:
    def __init__(self, name: str, log_level: str = 'INFO', log_dir: Union[str, Path, None] = None):
        """
        Инициализация логгера сервиса
        :param name: Имя сервиса/модуля
        :param log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        :param log_dir: Директория для хранения логов (None → DSIGN_LOG_DIR or /var/log/dsign)
        """
        # Allow environment override. Recommended on Pi: WARNING in production.
        env_level = os.getenv("DSIGN_LOG_LEVEL")
        effective_level = (env_level or log_level or "INFO").upper()

        self.logger = logging.getLogger(name)
        self.logger.setLevel(effective_level)
        # Prevent double-logging via root logger handlers.
        self.logger.propagate = False
        
        # Форматтер с JSON-подобным выводом
        self.formatter = logging.Formatter(
            '{"time": "%(asctime)s", "service": "%(name)s", "level": "%(levelname)s", "message": %(message)s}',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Avoid adding duplicate handlers if this logger is constructed more than once
        # (e.g., app init + service factories). If handlers already exist, only update level/formatter.
        if not self.logger.handlers:
            # Консольный вывод
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(self.formatter)
            self.logger.addHandler(console_handler)

            # Файловый вывод с ротацией (avoid cwd-relative dirs: systemd WorkingDirectory may not be writable)
            resolved = log_dir if log_dir is not None else os.getenv("DSIGN_LOG_DIR") or "/var/log/dsign"
            candidates = [Path(resolved).expanduser()]
            try:
                home = Path.home()
                candidates.append(home / ".cache" / "dsign" / "logs")
            except Exception:
                pass

            file_handler = None
            for cand in candidates:
                try:
                    cand.mkdir(parents=True, exist_ok=True)
                    fh = RotatingFileHandler(
                        cand / f'{name}.log',
                        maxBytes=10 * 1024 * 1024,  # 10 MB
                        backupCount=5,
                        encoding='utf-8',
                    )
                    fh.setFormatter(self.formatter)
                    file_handler = fh
                    break
                except OSError:
                    continue

            if file_handler is not None:
                self.logger.addHandler(file_handler)
        else:
            for h in self.logger.handlers:
                try:
                    h.setFormatter(self.formatter)
                except Exception:
                    pass

    def _format_message(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> str:
        """
        Форматирование сообщения и метаданных в JSON строку
        """
        message_data = {'text': msg}
        if extra:
            message_data.update(extra)
        return json.dumps(message_data, ensure_ascii=False)

    def debug(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        # kwargs accepted for compatibility with logging.Logger
        self.logger.debug(self._format_message(msg, extra))

    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.info(self._format_message(msg, extra))

    def warning(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.warning(self._format_message(msg, extra))

    def error(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Compatible with logging.Logger.error(..., exc_info=True).

        Our JSON formatter doesn't have direct access to exception state, so if exc_info is requested,
        we attach a best-effort formatted traceback into the JSON payload.
        """
        try:
            if kwargs.get("exc_info"):
                extra = dict(extra or {})
                extra.setdefault("traceback", traceback.format_exc())
        except Exception:
            pass
        self.logger.error(self._format_message(msg, extra))

    def critical(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        try:
            if kwargs.get("exc_info"):
                extra = dict(extra or {})
                extra.setdefault("traceback", traceback.format_exc())
        except Exception:
            pass
        self.logger.critical(self._format_message(msg, extra))

    def exception(self, msg, *args, **kwargs):
        """Совместимость с logging.Logger.exception (в т.ч. app.logger.exception(...))."""
        return self.logger.exception(msg, *args, **kwargs)

def setup_logger(name: str, **kwargs) -> ServiceLogger:
    """
    Фабрика для создания логгеров сервисов
    :param name: Имя сервиса/модуля
    :param kwargs: Дополнительные параметры (log_level, log_dir)
    :return: Экземпляр ServiceLogger
    """
    return ServiceLogger(name, **kwargs)

def setup_flask_logging(app: Flask):
    """
    Настройка логирования для Flask приложения
    :param app: Экземпляр Flask приложения
    """
    # Удаляем стандартные обработчики Flask
    for handler in list(app.logger.handlers):
        app.logger.removeHandler(handler)
    
    # Создаем и настраиваем логгер
    service_logger = setup_logger('flask.app')
    
    # Перенаправляем логи Flask в наш логгер
    app.logger.handlers = service_logger.logger.handlers
    app.logger.setLevel(service_logger.logger.level)
    
    # Отключаем propagate чтобы избежать дублирования логов
    app.logger.propagate = False

    # Quiet down noisy libraries unless app is explicitly in DEBUG.
    # (Flask-SocketIO/EngineIO can spam "emitting event ..." otherwise.)
    if not app.debug:
        for noisy in ("engineio", "socketio", "werkzeug"):
            try:
                logging.getLogger(noisy).setLevel(logging.WARNING)
            except Exception:
                pass
