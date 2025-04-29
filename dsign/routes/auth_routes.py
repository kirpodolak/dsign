from flask import render_template, redirect, url_for, flash, request, jsonify, current_app, make_response, Blueprint
from flask_login import login_user, logout_user, login_required, current_user
from ..forms import LoginForm
from ..models import User
import logging

logger = logging.getLogger(__name__)

def init_auth_routes(auth_bp):
    @auth_bp.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'redirect': url_for('main.index')})
            return redirect(url_for('main.index'))
            
        form = LoginForm()
        if form.validate_on_submit():
            try:
                user = User.query.filter_by(username=form.username.data).first()
                
                if not user:
                    logger.warning(f"Login attempt for non-existent user {form.username.data}")
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
                    flash('Invalid username or password', 'error')
                    return redirect(url_for('auth.login'))
                
                if not user.check_password(form.password.data):
                    logger.warning(f"Failed login attempt for user {form.username.data}")
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
                    flash('Invalid username or password', 'error')
                    return redirect(url_for('auth.login'))
                
                if not user.password.startswith(('$2a$', '$2b$')):
                    try:
                        user.upgrade_password(form.password.data)
                        logger.info(f"Password hash upgraded for user {user.username}")
                    except Exception as upgrade_error:
                        logger.error(f"Failed to upgrade password for {user.username}: {str(upgrade_error)}")
                
                remember = form.remember.data if hasattr(form, 'remember') else False
                login_user(user, remember=remember)
                logger.info(f"Successful login for user {user.username}")
                
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    response = jsonify({
                        'success': True,
                        'redirect': request.args.get('next') or url_for('main.index')
                    })
                    response.set_cookie(
                        current_app.config['SESSION_COOKIE_NAME'],
                        value=request.cookies.get(current_app.config['SESSION_COOKIE_NAME'], ''),
                        secure=current_app.config['SESSION_COOKIE_SECURE'],
                        httponly=True,
                        samesite='Lax'
                    )
                    return response
                
                next_page = request.args.get('next') or url_for('main.index')
                return redirect(next_page)
                
            except Exception as e:
                logger.error(f"Login system error: {str(e)}", exc_info=True)
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'error': 'System error'}), 500
                flash('System error during authentication', 'error')
                return redirect(url_for('auth.login'))
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'Invalid form data'}), 400
        return render_template('login.html', form=form)

    @auth_bp.route('/logout')
    @login_required
    def logout():
        username = current_user.username
        logout_user()
        logger.info(f"User {username} logged out")
        flash("You have been logged out.", "info")
        return redirect(url_for('auth.login'))

    @auth_bp.route('/api/check-auth')
    def check_auth():
        """Endpoint для проверки статуса аутентификации"""
        logger.debug(f"Check-auth called, auth: {current_user.is_authenticated}")
        return jsonify({
            'authenticated': current_user.is_authenticated,
            'username': current_user.username if current_user.is_authenticated else None,
            'user_id': current_user.id if current_user.is_authenticated else None
        })

    @auth_bp.route('/check-auth')
    def legacy_check_auth():
        """Старый endpoint для обратной совместимости"""
        return redirect(url_for('auth.check_auth'))

    @auth_bp.route('/api/users/<username>')
    @login_required
    def get_user_info(username):
        """Получение информации о пользователе"""
        user = User.query.filter_by(username=username).first()
        if not user:
            logger.warning(f"User info requested for non-existent user: {username}")
            return jsonify({'error': 'User not found'}), 404
            
        logger.debug(f"Returning info for user: {username}")
        return jsonify({
            'id': user.id,
            'username': user.username,
            'is_admin': user.is_admin
        })
        
    @auth_bp.route('/api/socket-token')
    @login_required
    def get_socket_token():
        return jsonify({
            'token': generate_socket_token(current_user.id),
            'expires': datetime.utcnow() + timedelta(minutes=5)
        })
