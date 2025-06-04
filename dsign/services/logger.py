"""
/dsign/service/logger.py
Модуль централизованного логирования сервисов с поддержкой Flask
"""

import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, Union
import json
from pathlib import Path
from datetime import datetime
from flask import Flask
import sys

class ServiceLogger:
    def __init__(self, name: str, log_level: str = 'INFO', log_dir: Union[str, Path] = 'logs'):
        """
        Инициализация логгера сервиса
        :param name: Имя сервиса/модуля
        :param log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        :param log_dir: Директория для хранения логов
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)
        
        # Форматтер с JSON-подобным выводом
        self.formatter = logging.Formatter(
            '{"time": "%(asctime)s", "service": "%(name)s", "level": "%(levelname)s", "message": %(message)s}',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Консольный вывод
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(self.formatter)
        self.logger.addHandler(console_handler)
        
        # Файловый вывод с ротацией
        log_dir = Path(log_dir)
        log_dir.mkdir(exist_ok=True)
        
        file_handler = RotatingFileHandler(
            log_dir / f'{name}.log',
            maxBytes=10*1024*1024,  # 10 MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)

    def _format_message(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> str:
        """
        Форматирование сообщения и метаданных в JSON строку
        """
        message_data = {'text': msg}
        if extra:
            message_data.update(extra)
        return json.dumps(message_data, ensure_ascii=False)

    def debug(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self.logger.debug(self._format_message(msg, extra))

    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self.logger.info(self._format_message(msg, extra))

    def warning(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self.logger.warning(self._format_message(msg, extra))

    def error(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self.logger.error(self._format_message(msg, extra))

    def critical(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self.logger.critical(self._format_message(msg, extra))

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
