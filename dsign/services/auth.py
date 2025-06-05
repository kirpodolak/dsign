from flask_login import LoginManager
import jwt
from datetime import datetime, timedelta
from flask import current_app, request, jsonify
from .logger import setup_logger

login_manager = LoginManager()

class AuthService:
    def __init__(self, secret_key, logger=None):
        """
        Инициализация сервиса аутентификации
        :param secret_key: Секретный ключ для JWT
        """
        self.secret_key = secret_key
        self.logger = logger or setup_logger('AuthService')
        self.login_attempts = {}
        self.max_attempts = 5
        self.lock_time = timedelta(minutes=15)
        self.token_expiration = timedelta(hours=24)  # 24 часа для стандартных токенов
        self.socket_token_expiration = timedelta(minutes=30)  # 30 минут для WebSocket токенов
        self.token_refresh_threshold = timedelta(minutes=5)  # Порог для обновления токена

    def generate_token(self, user_id, socket_token=False, refresh_token=False):
        """
        Улучшенная генерация JWT токенов
        :param user_id: ID пользователя
        :param socket_token: Флаг для генерации токена WebSocket
        :param refresh_token: Флаг для генерации refresh-токена
        :return: Сгенерированный токен
        """
        try:
            if refresh_token:
                expiration = timedelta(days=7)  # 7 дней для refresh-токена
                token_type = 'refresh'
            elif socket_token:
                expiration = self.socket_token_expiration
                token_type = 'socket'
            else:
                expiration = self.token_expiration
                token_type = 'access'

            payload = {
                'user_id': user_id,
                'exp': datetime.utcnow() + expiration,
                'iat': datetime.utcnow(),
                'iss': current_app.config.get('JWT_ISSUER', 'dsign-auth'),
                'type': token_type,
                'aud': 'socket-client' if socket_token else 'web-client'
            }

            # Добавляем роли для пользователя
            from ..models import User
            user = User.query.get(user_id)
            if user:
                payload['roles'] = [role.name for role in user.roles] if hasattr(user, 'roles') else []
                payload['is_admin'] = getattr(user, 'is_admin', False)

            token = jwt.encode(payload, self.secret_key, algorithm='HS256')

            self.logger.info('Token generated', {
                'user_id': user_id,
                'token_type': token_type,
                'expires': payload['exp'].isoformat()
            })
            return token

        except Exception as e:
            self.logger.error('Token generation failed', {
                'error': str(e),
                'stack': True
            })
            raise RuntimeError("Token generation failed") from e

    def verify_token(self, token, socket_token=False, refresh_token=False):
        """
        Улучшенная верификация JWT токена
        :param token: Токен для проверки
        :param socket_token: Проверять как WebSocket токен
        :param refresh_token: Проверять как refresh-токен
        :return: Декодированный payload или None
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=['HS256'],
                options={'verify_exp': True},
                audience='socket-client' if socket_token else 'web-client'
            )

            # Проверка типа токена
            if socket_token and payload.get('type') != 'socket':
                raise jwt.InvalidTokenError('Not a WebSocket token')
            if refresh_token and payload.get('type') != 'refresh':
                raise jwt.InvalidTokenError('Not a refresh token')
            if not (socket_token or refresh_token) and payload.get('type') != 'access':
                raise jwt.InvalidTokenError('Not an access token')

            # Проверка срока действия
            expires_at = datetime.fromtimestamp(payload['exp'])
            time_remaining = expires_at - datetime.utcnow()

            self.logger.debug('Token verified', {
                'user_id': payload['user_id'],
                'token_type': payload.get('type', 'unknown'),
                'expires': expires_at.isoformat(),
                'time_remaining': str(time_remaining)
            })
            return payload

        except jwt.ExpiredSignatureError:
            self.logger.warning('Token expired', {
                'token': token[:10] + '...'
            })
            return None
        except jwt.InvalidTokenError as e:
            self.logger.warning('Invalid token', {
                'error': str(e),
                'token': token[:10] + '...'
            })
            return None
        except Exception as e:
            self.logger.error('Token verification error', {
                'error': str(e),
                'stack': True
            })
            return None

    def should_refresh_token(self, token):
        """
        Проверка, нужно ли обновить токен
        :param token: Токен для проверки
        :return: True если нужно обновить
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=['HS256'],
                options={'verify_exp': False}  # Не проверяем expiration
            )
            expires_at = datetime.fromtimestamp(payload['exp'])
            return (expires_at - datetime.utcnow()) < self.token_refresh_threshold
        except Exception:
            return True

    def check_login_attempts(self, username):
        """
        Проверка количества попыток входа
        :param username: Имя пользователя
        :return: True если можно пробовать войти, False если аккаунт временно заблокирован
        """
        if username in self.login_attempts:
            attempts_info = self.login_attempts[username]
            if attempts_info['attempts'] >= self.max_attempts:
                if datetime.utcnow() < attempts_info['lock_time']:
                    self.logger.warning('Account temporarily locked', {
                        'username': username,
                        'lock_until': attempts_info['lock_time'].isoformat(),
                        'ip': request.remote_addr
                    })
                    return False
                else:
                    del self.login_attempts[username]
                    self.logger.info('Account lock expired', {
                        'username': username
                    })
        return True

    def record_failed_attempt(self, username):
        """
        Запись неудачной попытки входа
        :param username: Имя пользователя
        """
        ip_address = request.remote_addr
        
        if username not in self.login_attempts:
            self.login_attempts[username] = {
                'attempts': 0,
                'lock_time': None,
                'last_attempt': datetime.utcnow(),
                'ip': ip_address
            }
            
        self.login_attempts[username]['attempts'] += 1
        self.login_attempts[username]['last_attempt'] = datetime.utcnow()
        
        self.logger.warning('Failed login attempt', {
            'username': username,
            'attempt': self.login_attempts[username]['attempts'],
            'ip': ip_address
        })

        if self.login_attempts[username]['attempts'] >= self.max_attempts:
            lock_time = datetime.utcnow() + self.lock_time
            self.login_attempts[username]['lock_time'] = lock_time
            self.logger.warning('Account locked due to multiple failed attempts', {
                'username': username,
                'lock_until': lock_time.isoformat(),
                'ip': ip_address
            })

    def reset_login_attempts(self, username):
        """
        Сброс счетчика неудачных попыток входа
        :param username: Имя пользователя
        """
        if username in self.login_attempts:
            self.logger.info('Resetting failed attempts counter', {
                'username': username,
                'previous_attempts': self.login_attempts[username]['attempts']
            })
            del self.login_attempts[username]

    def get_current_user_status(self, user_id):
        """
        Получение текущего статуса пользователя
        :param user_id: ID пользователя
        :return: Словарь с информацией о статусе
        """
        from ..models import User
        user = User.query.get(user_id)
        if not user:
            return None
            
        return {
            'authenticated': True,
            'user_id': user.id,
            'username': user.username,
            'roles': [role.name for role in user.roles] if hasattr(user, 'roles') else [],
            'is_admin': getattr(user, 'is_admin', False),
            'timestamp': datetime.utcnow().isoformat()
        }

    def create_tokens(self, user_id):
        """
        Создание пары access и refresh токенов
        :param user_id: ID пользователя
        :return: Кортеж (access_token, refresh_token)
        """
        access_token = self.generate_token(user_id)
        refresh_token = self.generate_token(user_id, refresh_token=True)
        return access_token, refresh_token

@login_manager.user_loader
def load_user(user_id):
    """
    Callback для загрузки пользователя Flask-Login
    :param user_id: ID пользователя
    :return: Объект пользователя или None
    """
    from ..models import User
    try:
        user = User.query.get(int(user_id))
        if user:
            current_app.logger.debug('User loaded', {
                'user_id': user_id,
                'username': user.username
            })
        else:
            current_app.logger.warning('User not found', {
                'user_id': user_id
            })
        return user
        
    except (ValueError, TypeError) as e:
        current_app.logger.warning('Invalid user ID format', {
            'user_id': user_id,
            'error': str(e)
        })
        return None
    except Exception as e:
        current_app.logger.error('Error loading user', {
            'user_id': user_id,
            'error': str(e),
            'stack': True
        })
        return None
