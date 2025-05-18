from flask import render_template, redirect, url_for, flash, request, jsonify, current_app, make_response, Blueprint
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
import jwt
import logging
import os
from ..forms import LoginForm
from ..models import User
from functools import wraps

logger = logging.getLogger(__name__)

def init_auth_routes(auth_bp):
    # Rate limiting and security configurations
    MAX_LOGIN_ATTEMPTS = 5
    LOGIN_TIMEOUT = 300  # 5 minutes in seconds
    TOKEN_EXPIRATION = 60  # 1 hour in minutes
    SOCKET_TOKEN_EXPIRATION = 5  # 5 minutes in minutes

    # Track failed login attempts
    failed_attempts = {}

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
            if user and user.is_admin:
                payload['roles'].append('admin')

            return jwt.encode(
                payload,
                current_app.config['SECRET_KEY'],
                algorithm='HS256'
            )
        except Exception as e:
            logger.error(f"Token generation error: {str(e)}")
            raise

    def generate_socket_token(user_id):
        """Generate short-lived token for socket authentication"""
        return generate_jwt_token(user_id, expires_minutes=SOCKET_TOKEN_EXPIRATION)

    def check_login_attempts(ip, username):
        """Check if login attempts exceed the limit"""
        key = f"{ip}-{username}"
        now = datetime.utcnow()
        
        if key in failed_attempts:
            last_attempt, count = failed_attempts[key]
            if (now - last_attempt).seconds < LOGIN_TIMEOUT:
                if count >= MAX_LOGIN_ATTEMPTS:
                    logger.warning(f"Login attempts exceeded for {username} from {ip}")
                    return False
                failed_attempts[key] = (now, count + 1)
            else:
                failed_attempts[key] = (now, 1)
        else:
            failed_attempts[key] = (now, 1)
        
        return True

    def rate_limited(f):
        """Decorator for rate limiting endpoints"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            ip = request.remote_addr
            if not check_login_attempts(ip, request.form.get('username', '')):
                return jsonify({
                    'success': False,
                    'error': 'Too many login attempts. Please try again later.'
                }), 429
            return f(*args, **kwargs)
        return decorated_function

    @auth_bp.route('/login', methods=['GET', 'POST'])
    @rate_limited
    def login():
        if current_user.is_authenticated:
            if request.is_json:
                return jsonify({
                    'success': True,
                    'redirect': url_for('main.index'),
                    'user': {
                        'id': current_user.id,
                        'username': current_user.username,
                        'is_admin': current_user.is_admin
                    }
                })
            return redirect(url_for('main.index'))
            
        form = LoginForm()
        if form.validate_on_submit():
            try:
                user = User.query.filter_by(username=form.username.data).first()
                
                if not user:
                    logger.warning(f"Login attempt for non-existent user: {form.username.data} from IP: {request.remote_addr}")
                    if request.is_json:
                        return jsonify({
                            'success': False,
                            'error': 'Invalid credentials'
                        }), 401
                    flash('Invalid username or password', 'error')
                    return redirect(url_for('auth.login'))
                
                if not user.check_password(form.password.data):
                    logger.warning(f"Failed login attempt for user: {user.username} from IP: {request.remote_addr}")
                    if request.is_json:
                        return jsonify({
                            'success': False, 
                            'error': 'Invalid credentials'
                        }), 401
                    flash('Invalid username or password', 'error')
                    return redirect(url_for('auth.login'))
                
                # Upgrade legacy password hashes
                if user.needs_password_upgrade():
                    try:
                        user.password = generate_password_hash(form.password.data)
                        user.save()
                        logger.info(f"Upgraded password hash for user: {user.username}")
                    except Exception as e:
                        logger.error(f"Password upgrade failed for {user.username}: {str(e)}")

                # Generate tokens
                auth_token = generate_jwt_token(user.id)
                remember = form.remember.data if hasattr(form, 'remember') else False
                
                login_user(user, remember=remember)
                logger.info(f"Successful login for user: {user.username} from IP: {request.remote_addr}")

                response_data = {
                    'success': True,
                    'redirect': request.args.get('next') or url_for('main.index'),
                    'token': auth_token,
                    'user': {
                        'id': user.id,
                        'username': user.username,
                        'is_admin': user.is_admin,
                        'permissions': user.get_permissions()
                    }
                }

                # Prepare response
                if request.is_json:
                    response = jsonify(response_data)
                    response.headers.add('X-CSRF-Token', generate_csrf_token())
                else:
                    response = redirect(response_data['redirect'])
                
                # Set secure cookies
                response.set_cookie(
                    'authToken',
                    value=auth_token,
                    httponly=True,
                    secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
                    samesite='Lax',
                    max_age=3600 * 24 * 7 if remember else None,  # 7 days for "remember me"
                    domain=current_app.config.get('SESSION_COOKIE_DOMAIN'),
                    path='/'
                )
                
                # Clear failed attempts on successful login
                key = f"{request.remote_addr}-{user.username}"
                if key in failed_attempts:
                    del failed_attempts[key]
                
                return response
                
            except Exception as e:
                logger.error(f"Login system error: {str(e)}", exc_info=True)
                if request.is_json:
                    return jsonify({
                        'success': False,
                        'error': 'System error'
                    }), 500
                flash('System error during authentication', 'error')
                return redirect(url_for('auth.login'))
        
        if request.is_json:
            return jsonify({
                'success': False,
                'error': 'Invalid form data',
                'errors': form.errors
            }), 400
            
        return render_template('login.html', form=form)

    @auth_bp.route('/logout')
    @login_required
    def logout():
        username = current_user.username
        logout_user()
        
        response = redirect(url_for('auth.login'))
        response.delete_cookie('authToken')
        response.delete_cookie(current_app.config['SESSION_COOKIE_NAME'])
        response.headers.add('Clear-Site-Data', '"cookies", "storage"')
        
        logger.info(f"User logged out: {username} from IP: {request.remote_addr}")
        flash("You have been logged out.", "info")
        return response

    @auth_bp.route('/api/check-auth')
    def check_auth():
        """Enhanced authentication status endpoint with token validation"""
        try:
            token = request.cookies.get('authToken')
            token_valid = False
            
            if token:
                try:
                    jwt.decode(
                        token,
                        current_app.config['SECRET_KEY'],
                        algorithms=['HS256'],
                        audience=current_app.config.get('JWT_AUDIENCE', 'client-app')
                    )
                    token_valid = True
                except jwt.ExpiredSignatureError:
                    logger.debug("Token expired")
                except jwt.InvalidTokenError as e:
                    logger.debug(f"Invalid token: {str(e)}")
            
            logger.debug(
                f"Auth check - IP: {request.remote_addr}, "
                f"Authenticated: {current_user.is_authenticated}, "
                f"Token Valid: {token_valid}, "
                f"User-Agent: {request.user_agent}"
            )
            
            return jsonify({
                'authenticated': current_user.is_authenticated,
                'token_valid': token_valid,
                'user': {
                    'id': current_user.id,
                    'username': current_user.username,
                    'is_admin': current_user.is_admin,
                    'permissions': current_user.get_permissions() if current_user.is_authenticated else []
                } if current_user.is_authenticated else None
            })
            
        except Exception as e:
            logger.error(f"Auth check error: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Authentication check failed'
            }), 500

    @auth_bp.route('/api/socket-token')
    @login_required
    def get_socket_token():
        """Secure WebSocket token generation with IP binding"""
        try:
            # Include client IP in token to prevent token reuse
            payload = {
                'sub': current_user.id,
                'ip': request.remote_addr,
                'exp': datetime.utcnow() + timedelta(minutes=SOCKET_TOKEN_EXPIRATION)
            }
            
            token = jwt.encode(
                payload,
                current_app.config['SECRET_KEY'],
                algorithm='HS256'
            )
            
            logger.debug(f"Generated socket token for user: {current_user.username}")
            
            return jsonify({
                'success': True,
                'token': token,
                'expires_in': SOCKET_TOKEN_EXPIRATION * 60,
                'expires_at': (datetime.utcnow() + timedelta(minutes=SOCKET_TOKEN_EXPIRATION)).isoformat()
            })
        except Exception as e:
            logger.error(f"Socket token generation failed: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Token generation failed'
            }), 500

    @auth_bp.route('/api/verify-token', methods=['POST'])
    def verify_token():
        """Token verification endpoint for frontend validation"""
        try:
            token = request.json.get('token') or request.cookies.get('authToken')
            if not token:
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
            logger.error(f"Token verification error: {str(e)}")
            return jsonify({'valid': False}), 200

    @auth_bp.route('/api/users/<username>')
    @login_required
    def get_user_info(username):
        """Secure user information endpoint with permission checks"""
        try:
            if not (current_user.is_admin or current_user.username == username):
                logger.warning(
                    f"Unauthorized user info request by {current_user.username} "
                    f"for user {username} from IP: {request.remote_addr}"
                )
                return jsonify({
                    'success': False,
                    'error': 'Unauthorized'
                }), 403
                
            user = User.query.filter_by(username=username).first_or_404()
            
            logger.debug(f"User info returned for {username} to {current_user.username}")
            return jsonify({
                'success': True,
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'is_admin': user.is_admin,
                    'created_at': user.created_at.isoformat() if user.created_at else None,
                    'last_login': user.last_login.isoformat() if user.last_login else None
                }
            })
        except Exception as e:
            logger.error(f"User info error: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Failed to retrieve user information'
            }), 500

    @auth_bp.route('/api/refresh-token', methods=['POST'])
    @login_required
    def refresh_token():
        """Secure token refresh with old token validation"""
        try:
            old_token = request.cookies.get('authToken')
            if old_token:
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
            
            logger.debug(f"Token refreshed for user: {current_user.username}")
            return response
            
        except Exception as e:
            logger.error(f"Token refresh failed: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Token refresh failed'
            }), 500
