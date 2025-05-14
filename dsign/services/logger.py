"""
/dsign/service/logger.py
Модуль централизованного логирования сервисов
"""

import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
import json
from datetime import datetime
from pathlib import Path

class ServiceLogger:
    def __init__(self, name: str, log_level: str = 'INFO'):
        """
        Инициализация логгера сервиса
        :param name: Имя сервиса/модуля
        :param log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)
        
        # Форматтер с JSON-подобным выводом
        self.formatter = logging.Formatter(
            '{"time": "%(asctime)s", "service": "%(name)s", "level": "%(levelname)s", "message": %(message)s}',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Консольный вывод
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self.formatter)
        self.logger.addHandler(console_handler)
        
        # Файловый вывод с ротацией
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        
        file_handler = RotatingFileHandler(
            log_dir / 'service.log',
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

def setup_logger(name: str) -> ServiceLogger:
    """
    Фабрика для создания логгеров сервисов
    :param name: Имя сервиса/модуля
    :return: Экземпляр ServiceLogger
    """
    return ServiceLogger(name)
