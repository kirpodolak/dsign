from flask import render_template, redirect, url_for, flash, request, jsonify, current_app, make_response, Blueprint
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
import jwt
import os
import traceback
from threading import Lock
from ..forms import LoginForm
from ..models import User
from functools import wraps
from ..services.logger import setup_logger
from urllib.parse import urlparse, urljoin

# Инициализация логгера
logger = setup_logger('auth.routes')

# Создаем Blueprint с уникальным именем
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# Security configurations
MAX_LOGIN_ATTEMPTS = 10
LOGIN_TIMEOUT = 180  # 3 minutes in seconds
TOKEN_EXPIRATION = 60  # 1 hour in minutes
SOCKET_TOKEN_EXPIRATION = 5  # 5 minutes in minutes

# Rate limiting storage with thread lock
rate_limit_data = {}
rate_limit_lock = Lock()

def _is_safe_next_url(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in ('http', 'https')
        and ref_url.netloc == test_url.netloc
    )

def _get_next_url(default: str):
    candidate = request.args.get('next') or request.form.get('next') or ''
    if _is_safe_next_url(candidate):
        return candidate
    return default

def _is_ajax_request() -> bool:
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

def _require_admin():
    if not (current_user.is_authenticated and getattr(current_user, 'is_admin', False)):
        return False
    return True

def generate_csrf_token():
    """Generate CSRF token for form protection"""
    if 'CSRF_TOKEN_SECRET' not in current_app.config:
        current_app.config['CSRF_TOKEN_SECRET'] = os.urandom(24).hex()
    
    return jwt.encode(
        {
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(minutes=30)
        },
        current_app.config['CSRF_TOKEN_SECRET'],
        algorithm='HS256'
    )

def generate_jwt_token(user_id, expires_minutes=TOKEN_EXPIRATION):
    """Generate JWT token with enhanced security claims"""
    try:
        if not user_id:
            raise ValueError("User ID is required for token generation")

        payload = {
            'sub': user_id,
            'exp': datetime.utcnow() + timedelta(minutes=expires_minutes),
            'iat': datetime.utcnow(),
            'iss': current_app.config.get('JWT_ISSUER', 'digital-signage'),
            'aud': current_app.config.get('JWT_AUDIENCE', 'client-app'),
            'jti': jwt.utils.base64url_encode(os.urandom(32)).decode(),
            'roles': ['user']  # Default role, can be extended
        }
        
        # Add additional claims for admin users
        user = User.query.get(user_id)
        if user and getattr(user, 'is_admin', False):
            payload['roles'].append('admin')

        if 'SECRET_KEY' not in current_app.config:
            raise ValueError("Secret key is not configured")

        return jwt.encode(
            payload,
            current_app.config['SECRET_KEY'],
            algorithm='HS256'
        )
    except Exception as e:
        logger.error("Token generation error", extra={
            'error': str(e),
            'user_id': user_id,
            'stack_trace': traceback.format_exc()
        })
        raise

def check_rate_limit(ip, username):
    """Custom rate limiting implementation"""
    if not ip or not username:
        return True
        
    key = f"{ip}-{username}"
    with rate_limit_lock:
        now = datetime.utcnow()
        if key not in rate_limit_data:
            rate_limit_data[key] = {'count': 1, 'start': now}
            return True
        
        time_diff = (now - rate_limit_data[key]['start']).total_seconds()
        if time_diff > LOGIN_TIMEOUT:
            rate_limit_data[key] = {'count': 1, 'start': now}
            return True
            
        if rate_limit_data[key]['count'] >= MAX_LOGIN_ATTEMPTS:
            return False
            
        rate_limit_data[key]['count'] += 1
        return True

def _username_for_rate_limit() -> str:
    """Имя пользователя только для POST-логина; без парсинга JSON на GET (пустое тело + application/json даёт 400)."""
    if request.method != 'POST':
        return ''
    if request.is_json:
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            u = data.get('username', '')
            return u if isinstance(u, str) else ''
    return request.form.get('username', '') or ''


def rate_limited(f):
    """Custom rate limiting decorator"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            ip = request.remote_addr or 'unknown'
            username = _username_for_rate_limit()
            
            if not check_rate_limit(ip, username):
                logger.warning("Rate limit exceeded", extra={
                    'ip': ip,
                    'username': username,
                    'endpoint': request.endpoint
                })
                return jsonify({
                    'success': False,
                    'error': 'Too many requests. Please try again later.'
                }), 429
            return f(*args, **kwargs)
        except Exception as e:
            logger.error("Rate limiting error", extra={
                'error': str(e),
                'stack_trace': traceback.format_exc()
            })
            return jsonify({
                'success': False,
                'error': 'Internal server error'
            }), 500
    return decorated_function

@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limited
def login():
    try:
        # Redirect if already authenticated
        if current_user.is_authenticated:
            next_url = _get_next_url(url_for('main.index'))
            if request.is_json:
                return jsonify({
                    'success': True,
                    'redirect': next_url,
                    'user': {
                        'id': current_user.id,
                        'username': current_user.username,
                        'is_admin': current_user.is_admin
                    }
                })
            return redirect(next_url)
            
        form = LoginForm()
        
        if request.method == 'POST':
            if request.is_json:
                form = LoginForm(data=request.json)
            
            if form.validate():
                user = User.query.filter_by(username=form.username.data).first()
                
                if not user or not user.check_password(form.password.data):
                    logger.warning("Failed login attempt", extra={
                        'username': form.username.data,
                        'ip': request.remote_addr,
                        'user_agent': request.user_agent.string
                    })
                    if request.is_json:
                        return jsonify({
                            'success': False,
                            'error': 'Invalid credentials'
                        }), 401
                    flash('Invalid username or password', 'error')
                    return redirect(url_for('auth.login'))
                
                # Password upgrade for legacy users
                if hasattr(user, 'needs_password_upgrade') and user.needs_password_upgrade():
                    try:
                        user.password = generate_password_hash(form.password.data)
                        user.save()
                        logger.info("Password hash upgraded", extra={
                            'username': user.username
                        })
                    except Exception as e:
                        logger.error("Password upgrade failed", extra={
                            'username': user.username,
                            'error': str(e),
                            'stack_trace': traceback.format_exc()
                        })

                remember = form.remember.data if hasattr(form, 'remember') else False
                
                login_user(user, remember=remember)
                logger.info("Successful login", extra={
                    'username': user.username,
                    'ip': request.remote_addr,
                    'remember': remember
                })

                response_data = {
                    'success': True,
                    'redirect': _get_next_url(url_for('main.index')),
                    'user': {
                        'id': user.id,
                        'username': user.username,
                        'is_admin': user.is_admin,
                        'permissions': user.get_permissions() if hasattr(user, 'get_permissions') else []
                    }
                }

                # Response handling
                if request.is_json or _is_ajax_request():
                    response = jsonify(response_data)
                else:
                    response = redirect(response_data['redirect'])
                
                # No JS-readable tokens; rely on Flask session/remember cookies.
                
                # Clear rate limiting
                key = f"{request.remote_addr}-{user.username}"
                if key in rate_limit_data:
                    del rate_limit_data[key]
                
                return response
            
            # Form validation errors
            if request.is_json:
                return jsonify({
                    'success': False,
                    'error': 'Invalid form data',
                    'errors': form.errors
                }), 400
        
        # GET request handling
        if request.is_json:
            return jsonify({
                'success': False,
                'error': 'Method not allowed'
            }), 405
            
        return render_template('auth/login.html', form=form)
        
    except Exception as e:
        logger.error("Login system error", extra={
            'error': str(e),
            'stack_trace': traceback.format_exc(),
            'ip': request.remote_addr
        })
        if request.is_json:
            return jsonify({
                'success': False,
                'error': 'System error'
            }), 500
        flash('System error during authentication', 'error')
        return redirect(url_for('auth.login'))

@auth_bp.route('/logout')
@login_required
def logout():
    try:
        username = current_user.username if current_user.is_authenticated else 'unknown'
        logout_user()

        response = redirect(url_for('auth.login'))
        cfg = current_app.config

        # Remember-me: без secure/samesite/path как при установке cookie браузер часто не сбрасывает
        # cookie → Flask-Login снова поднимает пользователя из remember_token → редирект с /login на /.
        remember_name = cfg.get('REMEMBER_COOKIE_NAME', 'remember_token')
        response.delete_cookie(
            remember_name,
            path=cfg.get('REMEMBER_COOKIE_PATH', '/'),
            domain=cfg.get('REMEMBER_COOKIE_DOMAIN'),
            secure=cfg.get('REMEMBER_COOKIE_SECURE', False),
            httponly=cfg.get('REMEMBER_COOKIE_HTTPONLY', True),
            samesite=cfg.get('REMEMBER_COOKIE_SAMESITE'),
        )

        response.delete_cookie('authToken', path='/')
        # Сессию обновляет Flask после logout_user(); отдельный delete_cookie(session) ломает flash и
        # конфликтует с Set-Cookie. Clear-Site-Data убран — редирект мог уйти до фактической очистки.

        logger.info("User logged out", extra={
            'username': username,
            'ip': request.remote_addr
        })
        flash("You have been logged out.", "info")
        return response
    except Exception as e:
        logger.error("Logout error", extra={
            'error': str(e),
            'stack_trace': traceback.format_exc()
        })
        return redirect(url_for('auth.login'))

@auth_bp.route('/check-auth')
def check_auth():
    """Enhanced authentication status endpoint with token validation"""
    try:
        token = request.cookies.get('authToken')
        token_valid = False
        
        if token and 'SECRET_KEY' in current_app.config:
            try:
                jwt.decode(
                    token,
                    current_app.config['SECRET_KEY'],
                    algorithms=['HS256'],
                    audience=current_app.config.get('JWT_AUDIENCE', 'client-app')
                )
                token_valid = True
            except jwt.ExpiredSignatureError:
                logger.debug("Token expired", extra={
                    'ip': request.remote_addr
                })
            except jwt.InvalidTokenError as e:
                logger.debug("Invalid token", extra={
                    'error': str(e),
                    'ip': request.remote_addr
                })
        
        user_data = None
        if current_user.is_authenticated:
            user_data = {
                'id': current_user.id,
                'username': current_user.username,
                'is_admin': getattr(current_user, 'is_admin', False),
                'permissions': current_user.get_permissions() if hasattr(current_user, 'get_permissions') else []
            }
        
        logger.debug("Auth check", extra={
            'ip': request.remote_addr,
            'authenticated': current_user.is_authenticated,
            'token_valid': token_valid,
            'user_agent': request.user_agent.string
        })
        
        return jsonify({
            'authenticated': current_user.is_authenticated,
            'token_valid': token_valid,
            'user': user_data
        })
        
    except Exception as e:
        logger.error("Auth check error", extra={
            'error': str(e),
            'stack_trace': traceback.format_exc(),
            'ip': request.remote_addr
        })
        return jsonify({
            'success': False,
            'error': 'Authentication check failed'
        }), 500

@auth_bp.route('/socket-token')
@login_required
def get_socket_token():
    """Secure WebSocket token generation with enhanced validation"""
    try:
        if not current_user.is_authenticated:
            logger.warning("Unauthorized socket token request", extra={
                'ip': request.remote_addr,
                'user_agent': request.user_agent.string
            })
            return jsonify({
                'success': False,
                'error': 'Not authenticated',
                'login_url': url_for('auth.login')
            }), 401

        # Best-effort server-side cache to avoid token-generation storms on reconnects/navigation.
        # NOTE: Flask `session` is dict-like (SecureCookieSession), but not necessarily a `dict`.
        # Token is short-lived; re-issue only when close to expiry or when client fingerprint changes.
        try:
            cached = session.get('socket_token_cache')
        except Exception:
            cached = None

        ip_now = request.remote_addr or 'unknown'
        ua_now = (request.user_agent.string or '')[:200]
        now_ts = datetime.utcnow().timestamp()
        if isinstance(cached, dict):
            try:
                token_cached = cached.get('token')
                exp_ts = float(cached.get('exp_ts') or 0)
                cached_ip = cached.get('ip') or ''
                cached_ua = cached.get('ua') or ''
                # Refresh 30s before expiry
                if (
                    isinstance(token_cached, str)
                    and token_cached
                    and exp_ts > (now_ts + 30)
                    and cached_ip == ip_now
                    and cached_ua == ua_now
                ):
                    return jsonify({
                        'success': True,
                        'token': token_cached,
                        'expires_in': int(exp_ts - now_ts),
                        'expires_at': datetime.utcfromtimestamp(exp_ts).isoformat(),
                        'socket_url': current_app.config.get('SOCKET_SERVER_URL', '/socket.io'),
                        'cached': True
                    })
            except Exception:
                # Ignore cache errors and continue to generate a new token.
                pass

        # Generate token with additional security claims
        payload = {
            'sub': str(current_user.id),
            # Backward compatibility: some socket handlers expect user_id (not sub)
            'user_id': current_user.id,
            # Required by SocketService validator (prevents token confusion with other JWT types)
            'purpose': 'socket_connection',
            'ip': request.remote_addr or 'unknown',
            'user_agent': request.user_agent.string[:200],
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(minutes=SOCKET_TOKEN_EXPIRATION),
            'iss': current_app.config.get('SOCKET_JWT_ISSUER', 'media-server'),
            'aud': 'socket-client',
            'jti': os.urandom(16).hex()
        }
        
        token = jwt.encode(
            payload,
            current_app.config['SECRET_KEY'],
            algorithm='HS256'
        )

        # pyjwt may return bytes depending on version; Flask session must store JSON-serializable types.
        if isinstance(token, (bytes, bytearray)):
            token = token.decode('utf-8', errors='ignore')
        
        logger.info("Socket token generated", extra={
            'user_id': current_user.id,
            'ip': request.remote_addr
        })

        # Store in session cache for this browser session.
        try:
            exp_ts = payload['exp'].timestamp()
            session['socket_token_cache'] = {
                'token': token,
                'exp_ts': exp_ts,
                'ip': ip_now,
                'ua': ua_now,
            }
            try:
                session.modified = True
            except Exception:
                pass
        except Exception:
            pass
        
        return jsonify({
            'success': True,
            'token': token,
            'expires_in': SOCKET_TOKEN_EXPIRATION * 60,
            'expires_at': payload['exp'].isoformat(),
            'socket_url': current_app.config.get('SOCKET_SERVER_URL', '/socket.io'),
            'cached': False,
        })
    except Exception as e:
        logger.error("Socket token generation failed", extra={
            'user_id': current_user.id if current_user.is_authenticated else None,
            'error': str(e),
            'stack_trace': traceback.format_exc()
        })
        return jsonify({
            'success': False,
            'error': 'Token generation failed',
            'details': str(e)
        }), 500

@auth_bp.route('/check-socket-auth')
def check_socket_auth():
    """Endpoint to check if client can connect to socket"""
    try:
        if not current_user.is_authenticated:
            return jsonify({
                'can_connect': False,
                'auth_required': True,
                'login_url': url_for('auth.login')
            }), 200
            
        return jsonify({
            'can_connect': True,
            'auth_required': False,
            'user_id': current_user.id,
            'socket_token_url': url_for('auth.get_socket_token')
        }), 200
    except Exception as e:
        logger.error("Socket auth check failed", extra={
            'error': str(e),
            'stack_trace': traceback.format_exc()
        })
        return jsonify({
            'can_connect': False,
            'error': str(e) 
        }), 500

@auth_bp.route('/verify-token', methods=['POST'])
def verify_token():
    """Token verification endpoint for frontend validation"""
    try:
        token = request.json.get('token') or request.cookies.get('authToken')
        if not token or 'SECRET_KEY' not in current_app.config:
            return jsonify({'valid': False}), 200
            
        jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=['HS256'],
            audience=current_app.config.get('JWT_AUDIENCE', 'client-app')
        )
        return jsonify({'valid': True}), 200
    except jwt.ExpiredSignatureError:
        return jsonify({'valid': False, 'expired': True}), 200
    except jwt.InvalidTokenError:
        return jsonify({'valid': False}), 200
    except Exception as e:
        logger.error("Token verification error", extra={
            'error': str(e),
            'stack_trace': traceback.format_exc(),
            'ip': request.remote_addr
        })
        return jsonify({'valid': False}), 200

@auth_bp.route('/users/<username>')
@login_required
def get_user_info(username):
    """Secure user information endpoint with permission checks"""
    try:
        if not current_user.is_authenticated:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401

        if not (getattr(current_user, 'is_admin', False) or current_user.username == username):
            logger.warning("Unauthorized user info request", extra={
                'requested_by': current_user.username,
                'requested_user': username,
                'ip': request.remote_addr
            })
            return jsonify({
                'success': False,
                'error': 'Unauthorized'
            }), 403
            
        user = User.query.filter_by(username=username).first_or_404()
        
        logger.debug("User info returned", extra={
            'requested_by': current_user.username,
            'requested_user': username
        })
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': getattr(user, 'email', None),
                'is_admin': getattr(user, 'is_admin', False),
                'created_at': user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None,
                'last_login': user.last_login.isoformat() if hasattr(user, 'last_login') and user.last_login else None
            }
        })
    except Exception as e:
        logger.error("User info error", extra={
            'username': username,
            'error': str(e),
            'stack_trace': traceback.format_exc()
        })
        return jsonify({
            'success': False,
            'error': 'Failed to retrieve user information'
        }), 500

@auth_bp.route('/refresh-token', methods=['POST'])
@login_required
def refresh_token():
    """Secure token refresh with old token validation"""
    try:
        if not current_user.is_authenticated:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401

        old_token = request.cookies.get('authToken')
        if old_token and 'SECRET_KEY' in current_app.config:
            try:
                jwt.decode(
                    old_token,
                    current_app.config['SECRET_KEY'],
                    algorithms=['HS256'],
                    audience=current_app.config.get('JWT_AUDIENCE', 'client-app')
                )
            except jwt.ExpiredSignatureError:
                pass  # Allow refresh of expired tokens
            except jwt.InvalidTokenError:
                return jsonify({
                    'success': False,
                    'error': 'Invalid existing token'
                }), 401
        
        new_token = generate_jwt_token(current_user.id)
        
        response = jsonify({
            'success': True,
            'token': new_token,
            'expires_in': TOKEN_EXPIRATION * 60
        })
        
        response.set_cookie(
            'authToken',
            value=new_token,
            httponly=True,
            secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
            samesite='Lax',
            max_age=3600,
            domain=current_app.config.get('SESSION_COOKIE_DOMAIN'),
            path='/'
        )
        
        logger.debug("Token refreshed", extra={
            'username': current_user.username
        })
        return response
        
    except Exception as e:
        logger.error("Token refresh failed", extra={
            'user_id': current_user.id if current_user.is_authenticated else None,
            'error': str(e),
            'stack_trace': traceback.format_exc()
        })
        return jsonify({
            'success': False,
            'error': 'Token refresh failed'
        }), 500

@auth_bp.route('/reset-limits', methods=['POST'])
@login_required
def reset_limits():
    """Reset rate limiting counters (for testing)"""
    try:
        if not _require_admin():
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        global rate_limit_data
        rate_limit_data = {}
        logger.info("Rate limits reset")
        return jsonify({'success': True})
    except Exception as e:
        logger.error("Reset limits failed", extra={
            'error': str(e),
            'stack_trace': traceback.format_exc()
        })
        return jsonify({
            'success': False,
            'error': 'Failed to reset limits'
        }), 500

@auth_bp.route('/status')
def auth_status():
    """Authentication status endpoint"""
    if not current_user.is_authenticated:
        return jsonify({'authenticated': False}), 401
    
    return jsonify({
        'authenticated': True,
        'user': {
            'id': current_user.id,
            'username': current_user.username,
            'is_admin': current_user.is_admin
        }
    })
