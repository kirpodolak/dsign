from flask_login import LoginManager
import jwt
from datetime import datetime, timedelta
from flask import current_app, request
from .logger import setup_logger

login_manager = LoginManager()

class AuthService:
    def __init__(self, secret_key, , logger=None):
        """
        Инициализация сервиса аутентификации
        :param secret_key: Секретный ключ для JWT
        """
        self.secret_key = secret_key
        self.logger = setup_logger('AuthService')
        self.login_attempts = {}
        self.max_attempts = 5
        self.lock_time = timedelta(minutes=15)
        self.token_expiration = timedelta(days=1)

    def generate_token(self, user_id):
        """
        Генерация JWT токена
        :param user_id: ID пользователя
        :return: Сгенерированный токен
        """
        try:
            payload = {
                'user_id': user_id,
                'exp': datetime.utcnow() + self.token_expiration,
                'iat': datetime.utcnow(),
                'iss': current_app.config.get('JWT_ISSUER', 'dsign-auth')
            }
            token = jwt.encode(payload, self.secret_key, algorithm='HS256')
            
            self.logger.info('Token generated', {
                'user_id': user_id,
                'token_exp': payload['exp'].isoformat()
            })
            return token
            
        except Exception as e:
            self.logger.error('Token generation failed', {
                'error': str(e),
                'stack': True
            })
            raise RuntimeError("Token generation failed") from e

    def verify_token(self, token):
        """
        Верификация JWT токена
        :param token: Токен для проверки
        :return: Декодированный payload или None
        """
        try:
            payload = jwt.decode(
                token, 
                self.secret_key, 
                algorithms=['HS256'],
                options={'verify_exp': True}
            )
            
            self.logger.debug('Token verified', {
                'user_id': payload['user_id'],
                'expires': datetime.fromtimestamp(payload['exp']).isoformat()
            })
            return payload
            
        except jwt.ExpiredSignatureError:
            self.logger.warning('Token expired', {
                'token': token[:10] + '...'  # Логируем только часть токена
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
