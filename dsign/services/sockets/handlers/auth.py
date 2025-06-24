# services/sockets/handlers/auth.py
from flask_socketio import Namespace, emit, disconnect
from flask import request
from datetime import datetime
import jwt
from ...logger import ServiceLogger

class AuthHandler(Namespace):
    """Namespace dedicated for authentication"""
    
    def __init__(self, namespace, socket_service):
        super().__init__(namespace)
        self.socket_service = socket_service
        self.logger = socket_service.logger

    def on_connect(self):
        """Handle initial connection to auth namespace"""
        self.logger.debug('Auth namespace connection', {
            'sid': request.sid,
            'ip': request.remote_addr
        })
        
        emit('auth_ready', {
            'timestamp': datetime.utcnow().isoformat()
        })

    def on_authenticate(self, data):
        """Handle authentication request"""
        try:
            if not data or not isinstance(data, dict):
                raise ValueError('Invalid authentication data')
                
            token = data.get('token')
            if not token:
                raise ValueError('Authentication token is required')
                
            # Use existing token verification from system service
            decoded = self.socket_service.verify_socket_token(token)
            user_id = decoded['user_id']
            
            # Register client in system service
            self.socket_service.register_authenticated_client(
                request.sid,
                user_id,
                request.namespace,
                request.remote_addr
            )
            
            emit('authentication_result', {
                'success': True,
                'user_id': user_id,
                'timestamp': datetime.utcnow().isoformat()
            })
            
        except jwt.ExpiredSignatureError:
            self._handle_auth_error('Token expired')
        except jwt.InvalidTokenError as e:
            self._handle_auth_error(f'Invalid token: {str(e)}')
        except Exception as e:
            self._handle_auth_error(f'Authentication failed: {str(e)}')

    def _handle_auth_error(self, error_msg):
        """Handle authentication errors"""
        self.logger.error('Authentication failed', {
            'error': error_msg,
            'sid': request.sid,
            'ip': request.remote_addr
        })
        
        emit('authentication_result', {
            'success': False,
            'error': error_msg,
            'timestamp': datetime.utcnow().isoformat()
        })
        
        disconnect()
