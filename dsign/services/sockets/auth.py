# services/sockets/auth.py
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from flask import current_app
import jwt
from threading import Lock
from ..logger import ServiceLogger

class SocketAuthService:
    """
    Специализированный сервис аутентификации для WebSocket соединений
    Наследует или использует основной AuthService для базовых операций
    """
    def __init__(self, auth_service, logger: Optional[ServiceLogger] = None):
        self.auth_service = auth_service  # Основной AuthService
        self.logger = logger or ServiceLogger('SocketAuthService')
        self.clients_lock = Lock()
        self.socket_auth_timeout = 30  # 30 секунд на аутентификацию

    def generate_socket_token(self, user_id: int) -> Dict[str, str]:
        """Генерация токена специально для WebSocket"""
        return self.auth_service.generate_token(
            user_id=user_id,
            socket_token=True
        )

    def verify_socket_token(self, token: str) -> Dict:
        """Верификация WebSocket токена"""
        try:
            payload = self.auth_service.verify_token(
                token=token,
                socket_token=True
            )
            if not payload:
                raise ValueError("Invalid socket token")
            return payload
        except Exception as e:
            self.logger.error('Socket token verification failed', {
                'error': str(e),
                'stack': True
            })
            raise

    def check_auth_timeout(self, client_info: Dict) -> bool:
        """
        Проверка превышения времени аутентификации для WebSocket
        """
        if not client_info.get('authenticated', False):
            connect_time = (datetime.utcnow() - client_info.get('connected_at')).seconds
            return connect_time > self.socket_auth_timeout
        return False

    def get_socket_auth_payload(self, user_id: int) -> Dict:
        """
        Формирование payload для аутентификации через WebSocket
        """
        from ...models import User
        user = User.query.get(user_id)
        if not user:
            raise ValueError("User not found")
            
        return {
            'authenticated': True,
            'user_id': user.id,
            'socket_token': self.generate_socket_token(user.id),
            'timestamp': datetime.utcnow().isoformat()
        }
