# dsign/services/auth_middleware.py
from flask import request, jsonify, redirect, url_for
from flask_login import current_user

def init_auth_middleware(app):
    @app.before_request
    def check_authentication():
        excluded_endpoints = ['auth.login', 'auth.logout', 'static']
        
        if request.endpoint in excluded_endpoints:
            return
        
        if not current_user.is_authenticated:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('auth.login', next=request.full_path))
