from flask_login import LoginManager
import jwt
from datetime import datetime, timedelta
from flask import current_app

login_manager = LoginManager()

class AuthService:
    def __init__(self, secret_key, logger=None):
        self.secret_key = secret_key
        self.logger = logger or current_app.logger
        self.login_attempts = {}
        self.max_attempts = 5
        self.lock_time = timedelta(minutes=15)

    def generate_token(self, user_id):
        try:
            payload = {
                'user_id': user_id,
                'exp': datetime.utcnow() + timedelta(days=1),
                'iat': datetime.utcnow()
            }
            token = jwt.encode(payload, self.secret_key, algorithm='HS256')
            self.logger.info(f"Token generated for user {user_id}")
            return token
        except Exception as e:
            self.logger.error(f"Token generation error: {str(e)}")
            raise

    def verify_token(self, token):
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=['HS256'])
            if datetime.utcnow() > datetime.fromtimestamp(payload['exp']):
                return None
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
        except Exception as e:
            self.logger.error(f"Token verification error: {str(e)}")
            return None

    def check_login_attempts(self, username):
        if username in self.login_attempts:
            if self.login_attempts[username]['attempts'] >= self.max_attempts:
                if datetime.utcnow() < self.login_attempts[username]['lock_time']:
                    return False
                else:
                    del self.login_attempts[username]
        return True

    def record_failed_attempt(self, username):
        if username not in self.login_attempts:
            self.login_attempts[username] = {
                'attempts': 0,
                'lock_time': None
            }
        self.login_attempts[username]['attempts'] += 1
        
        if self.login_attempts[username]['attempts'] >= self.max_attempts:
            self.login_attempts[username]['lock_time'] = datetime.utcnow() + self.lock_time

@login_manager.user_loader
def load_user(user_id):
    from ..models import User
    try:
        return User.query.get(int(user_id))
    except (ValueError, TypeError):
        return None
    except Exception as e:
        current_app.logger.error(f"Error loading user {user_id}: {str(e)}")
        return None