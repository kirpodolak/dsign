import socket
import os
from functools import lru_cache
from typing import Optional

class NetworkUtils:
    @staticmethod
    @lru_cache(maxsize=1)
    def get_local_ip() -> str:
        """Определяет локальный IP адрес (с кэшированием)"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(('8.8.8.8', 80))  # Google DNS
                return s.getsockname()[0]
        except Exception:
            return '127.0.0.1'

class PathUtils:
    @staticmethod
    def ensure_directory_exists(path: str) -> str:
        """
        Создает директорию если она не существует
        :param path: Путь к директории
        :return: Абсолютный путь к директории
        """
        os.makedirs(path, exist_ok=True)
        return os.path.abspath(path)