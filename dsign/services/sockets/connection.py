# services/sockets/connection.py
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from flask_socketio import SocketIO, disconnect, Namespace, emit
from flask import request, current_app
import jwt
import traceback
from ..logger import ServiceLogger
from threading import Lock
import time

class AuthNamespace(Namespace):
    """
    Special namespace for handling authentication
    """
    def __init__(self, namespace, socket_service):
        super().__init__(namespace)
        self.socket_service = socket_service
        self.logger = socket_service.logger

    def on_connect(self):
        """Connection handler for /auth namespace"""
        self.logger.debug('New auth namespace connection', {
            'sid': request.sid,
            'ip': request.remote_addr
        })
        
        # Send readiness confirmation to client
        self.emit('auth_ready', {
            'version': self.socket_service.server_version,
            'timestamp': datetime.utcnow().isoformat()
        })

    def on_authenticate(self, data):
        """Authentication handler for /auth namespace"""
        try:
            token = data.get('token')
            if not token:
                raise ValueError('Token is required')
            
            # Verify token
            decoded = self.socket_service.verify_socket_token(token)
            user_id = decoded['user_id']
            
            # Update client status
            with self.socket_service.clients_lock:
                if request.sid in self.socket_service.connected_clients:
                    self.socket_service.connected_clients[request.sid].update({
                        'user_id': user_id,
                        'authenticated': True,
                        'last_activity': datetime.utcnow()
                    })
                else:
                    self.logger.warning('Client not found during auth namespace auth', 
                                      {'sid': request.sid})
                    disconnect(request.sid)
                    return
            
            self.logger.info('User authenticated via auth namespace', {
                'user_id': user_id,
                'sid': request.sid
            })
            
            self.emit('authentication_result', {
                'success': True,
                'version': self.socket_service.server_version,
                'timestamp': datetime.utcnow().isoformat()
            })
            
        except Exception as e:
            error_msg = str(e)
            if isinstance(e, jwt.ExpiredSignatureError):
                error_msg = 'Token expired'
            elif isinstance(e, jwt.InvalidTokenError):
                error_msg = 'Invalid token'
                
            self.logger.error('Auth namespace authentication failed', {
                'error': error_msg,
                'sid': request.sid
            })
            
            self.emit('authentication_result', {
                'success': False,
                'error': error_msg,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            if 'token' in str(e).lower() or 'auth' in str(e).lower():
                self.emit('auth_error', {
                    'message': error_msg,
                    'timestamp': datetime.utcnow().isoformat()
                })
                
            disconnect(request.sid)

class ConnectionService:
    def __init__(self, socketio: Optional[SocketIO] = None, db_session=None, app=None, logger=None):
        """
        Initialize socket service
        :param socketio: SocketIO instance (required)
        :param db_session: Database session
        :param app: Flask application instance
        :param logger: Logger
        """
        if socketio is None:
            raise ValueError("SocketIO instance is required")
            
        self.socketio = socketio
        self.db = db_session
        self.app = app or current_app._get_current_object() if current_app else None
        self.logger = logger or ServiceLogger('ConnectionService')
        self.connected_clients = {}
        self.clients_lock = Lock()
        self.activity_check_interval = 15  # seconds
        self.socket_auth_timeout = 30  # 30 seconds timeout to match frontend
        self.server_version = "1.0.0"
        
        # Configuration matching frontend
        self.config = {
            'max_retries': 5,
            'reconnect_attempts': 5,
            'reconnect_delay': 1000,
            'ping_interval': 25  # seconds to match frontend
        }

        # Register custom namespace for authentication
        self.socketio.on_namespace(AuthNamespace('/auth', self))
        
        self.setup_handlers()
        self.register_handlers()
        self.start_activity_checker()
        self.start_version_broadcaster()

    def setup_handlers(self):
        """Setup event handlers"""
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            """WebSocket connection handler"""
            try:
                # Get token from different sources
                token = (
                    request.args.get('token') or 
                    (auth.get('token') if auth and isinstance(auth, dict) else None) or
                    request.headers.get('Authorization', '').replace('Bearer ', '')
                )

                if not token:
                    emit('auth_required', {
                        'message': 'Token required',
                        'timestamp': datetime.utcnow().isoformat()
                    })
                    return False

                # Debug logging
                self.logger.debug('Token received', {
                    'token_type': str(type(token)),
                    'token_length': len(token) if token else 0
                })

                # If token came as object (e.g. from auth)
                if isinstance(token, dict):
                    token = token.get('token', '')

                # Convert token to string if it's bytes
                if isinstance(token, bytes):
                    token = token.decode('utf-8')

                # Check that token is non-empty string
                if not isinstance(token, str) or not token.strip():
                    raise ValueError('Empty or invalid token format')

                # Verify token
                decoded = jwt.decode(
                    token,
                    current_app.config['SECRET_KEY'],
                    algorithms=['HS256'],
                    options={
                        'verify_exp': True,
                        'verify_aud': True,
                        'verify_iss': True,
                        'require': ['user_id', 'purpose']
                    },
                    audience='socket-client',
                    issuer='media-server'
                )
                
                if decoded.get('purpose') != 'socket_connection':
                    raise ValueError('Invalid token purpose')
                
                # Save client information
                with self.clients_lock:
                    self.connected_clients[request.sid] = {
                        'user_id': decoded['user_id'],
                        'connected_at': datetime.utcnow(),
                        'last_activity': datetime.utcnow(),
                        'authenticated': True,
                        'namespace': request.namespace,
                        'ip': request.remote_addr
                    }
                
                self.logger.info('Client connected', {
                    'sid': request.sid,
                    'user_id': decoded['user_id'],
                    'namespace': request.namespace,
                    'ip': request.remote_addr
                })
                
                emit('connection_ack', {
                    'status': 'authenticated',
                    'user_id': decoded['user_id'],
                    'timestamp': datetime.utcnow().isoformat()
                })
                
                return True

            except jwt.ExpiredSignatureError:
                error_msg = 'Token expired'
                self.logger.warning('Connection rejected', {
                    'reason': error_msg,
                    'sid': request.sid,
                    'ip': request.remote_addr
                })
                emit('token_expired', {
                    'message': error_msg,
                    'timestamp': datetime.utcnow().isoformat()
                })
                return False
                
            except jwt.InvalidTokenError as e:
                error_msg = f'Invalid token: {str(e)}'
                self.logger.error('Connection rejected', {
                    'reason': error_msg,
                    'sid': request.sid,
                    'ip': request.remote_addr,
                    'error_type': type(e).__name__
                })
                emit('auth_error', {
                    'message': error_msg,
                    'timestamp': datetime.utcnow().isoformat()
                })
                return False
                
            except Exception as e:
                error_msg = f'Connection error: {str(e)}'
                self.logger.error('Connection failed', {
                    'error': error_msg,
                    'sid': request.sid,
                    'ip': request.remote_addr,
                    'error_type': type(e).__name__,
                    'stack_trace': str(traceback.format_exc())  # Convert to string
                })
                emit('connection_error', {
                    'message': error_msg,
                    'timestamp': datetime.utcnow().isoformat()
                })
                return False

    def init_app(self, app):
        """Initialize with Flask app (for backward compatibility)"""
        self.app = app
        self.logger.info("ConnectionService initialized with Flask app")

    def register_handlers(self):
        """Register all event handlers for main namespace"""
        self.socketio.on_event('disconnect', self.handle_disconnect)
        self.socketio.on_event('authenticate', self.handle_authentication)
        self.socketio.on_event('ping', self.handle_ping)
        self.socketio.on_event('heartbeat', self.handle_heartbeat)
        self.socketio.on_event('get_server_version', self.handle_get_version)
        self.socketio.on_event('request_auth_status', self.handle_auth_status_request)

    def start_activity_checker(self):
        """Start background client activity checker"""
        def activity_check():
            while True:
                self.socketio.sleep(self.activity_check_interval)
                try:
                    self.check_activity()
                except Exception as e:
                    self.logger.error('Activity check failed', {'error': str(e)})

        self.socketio.start_background_task(activity_check)

    def start_version_broadcaster(self):
        """Periodic server version broadcasting"""
        def broadcast_version():
            while True:
                self.socketio.sleep(300)  # Every 5 minutes
                try:
                    with self.clients_lock:
                        active_sids = [sid for sid in self.connected_clients 
                                     if self.is_authenticated(sid)]
                    
                    for sid in active_sids:
                        self.socketio.emit('server_version', {
                            'version': self.server_version,
                            'timestamp': datetime.utcnow().isoformat()
                        }, room=sid)
                except Exception as e:
                    self.logger.error('Version broadcast failed', {'error': str(e)})

        self.socketio.start_background_task(broadcast_version)

    def handle_auth_status_request(self, data: Dict, sid: Optional[str] = None):
        """Authentication status request handler"""
        sid = sid or request.sid
        with self.clients_lock:
            client = self.connected_clients.get(sid)
            if client:
                authenticated = client.get('authenticated', False)
                user_id = client.get('user_id')
                
                self.socketio.emit('auth_status_response', {
                    'authenticated': authenticated,
                    'user_id': user_id,
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)
                
                self.logger.debug('Auth status sent', {
                    'sid': sid,
                    'authenticated': authenticated
                })

    def broadcast_auth_status(self, user_id: Optional[int] = None):
        """Broadcast authentication status changes"""
        with self.clients_lock:
            if user_id:
                # Send to specific user only
                target_sids = [sid for sid, client in self.connected_clients.items() 
                              if client.get('user_id') == user_id]
            else:
                # Send to all authenticated clients
                target_sids = [sid for sid, client in self.connected_clients.items() 
                              if client.get('authenticated')]

        for sid in target_sids:
            self.handle_auth_status_request({}, sid=sid)

        self.logger.debug('Broadcast auth status', {
            'user_id': user_id,
            'recipients': len(target_sids)
        })

    def handle_disconnect(self):
        """Client disconnect handler"""
        sid = request.sid
        self.logger.info('Client disconnected', {
            'sid': sid,
            'namespace': request.namespace
        })
        self.cleanup_client(sid)

    def handle_ping(self, data=None):
        """Ping message handler"""
        sid = request.sid
        with self.clients_lock:
            if sid in self.connected_clients:
                self.connected_clients[sid]['last_activity'] = datetime.utcnow()
                self.logger.debug('Ping received', {'sid': sid})
                if data and isinstance(data, dict) and data.get('echo'):
                    return {
                        'pong': data['echo'],
                        'timestamp': datetime.utcnow().isoformat()
                    }
        return {
            'pong': 'ok',
            'timestamp': datetime.utcnow().isoformat()
        }

    def handle_heartbeat(self, data: Dict):
        """Heartbeat message handler"""
        sid = request.sid
        timestamp = data.get('timestamp')
        
        with self.clients_lock:
            if sid in self.connected_clients:
                self.connected_clients[sid]['last_activity'] = datetime.utcnow()
                self.logger.debug('Heartbeat received', {'sid': sid})
                
                if timestamp:
                    latency = int((time.time() * 1000) - timestamp)
                    return {
                        'latency': latency,
                        'timestamp': datetime.utcnow().isoformat()
                    }
        
        return {
            'error': 'Invalid heartbeat',
            'timestamp': datetime.utcnow().isoformat()
        }

    def handle_get_version(self):
        """Server version request handler"""
        sid = request.sid
        self.socketio.emit('server_version', {
            'version': self.server_version,
            'timestamp': datetime.utcnow().isoformat()
        }, room=sid)

    def handle_authentication(self, data: Dict):
        """WebSocket authentication handler for main namespace"""
        sid = request.sid
        try:
            token = self.verify_socket_token(data.get('token'))
            user_id = token['user_id']
            
            from ..models import User
            user = self.db.session.query(User).get(user_id)
            if not user:
                raise ValueError('User not found')
            
            with self.clients_lock:
                if sid in self.connected_clients:
                    self.connected_clients[sid].update({
                        'user_id': user_id,
                        'authenticated': True,
                        'last_activity': datetime.utcnow()
                    })
                else:
                    self.logger.warning('Client not found during authentication', {'sid': sid})
                    disconnect(sid)
                    return
            
            self.logger.info('User authenticated', {
                'user_id': user_id,
                'sid': sid
            })
            
            self.socketio.emit('authentication_result', {
                'success': True,
                'version': self.server_version,
                'timestamp': datetime.utcnow().isoformat()
            }, room=sid)
            
            # Send updated status after authentication
            self.handle_auth_status_request({}, sid=sid)
            
        except Exception as e:
            self.logger.error('Authentication failed', {
                'error': str(e),
                'sid': sid
            })
            
            error_msg = str(e)
            if isinstance(e, jwt.ExpiredSignatureError):
                error_msg = 'Token expired'
            elif isinstance(e, jwt.InvalidTokenError):
                error_msg = 'Invalid token'
                
            self.socketio.emit('authentication_result', {
                'success': False,
                'error': error_msg,
                'timestamp': datetime.utcnow().isoformat()
            }, room=sid)
            
            if 'token' in str(e).lower() or 'auth' in str(e).lower():
                self.socketio.emit('auth_error', {
                    'message': error_msg,
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)
                
            self.cleanup_client(sid)

    def cleanup_client(self, sid: str):
        """Clean up client resources"""
        with self.clients_lock:
            if sid in self.connected_clients:
                client_info = self.connected_clients[sid]
                user_id = client_info.get('user_id', 'unknown')
                self.logger.info('Cleaning up client resources', {
                    'sid': sid,
                    'user_id': user_id,
                    'namespace': client_info.get('namespace', '/')
                })
                del self.connected_clients[sid]
                disconnect(sid)

    def check_activity(self):
        """Check client activity"""
        now = datetime.utcnow()
        inactive_clients = []

        with self.clients_lock:
            for sid, client in self.connected_clients.items():
                last_active = (now - client['last_activity']).seconds
                
                if not client['authenticated']:
                    connect_time = (now - client['connect_time']).seconds
                    if connect_time > self.socket_auth_timeout:
                        inactive_clients.append(sid)
                        self.logger.warning('Authentication timeout', {
                            'sid': sid,
                            'namespace': client.get('namespace', '/')
                        })
                        self.socketio.emit('auth_timeout', {
                            'message': 'Authentication timeout',
                            'timestamp': datetime.utcnow().isoformat()
                        }, room=sid)
                elif last_active > 60:
                    inactive_clients.append(sid)
                    self.logger.warning('Client inactive', {
                        'sid': sid,
                        'inactive_seconds': last_active,
                        'namespace': client.get('namespace', '/')
                    })
                    self.socketio.emit('inactivity_timeout', {
                        'message': 'Disconnected due to inactivity',
                        'timestamp': datetime.utcnow().isoformat()
                    }, room=sid)

        for sid in inactive_clients:
            self.cleanup_client(sid)

    def is_authenticated(self, sid: str) -> bool:
        """Check if client is authenticated"""
        with self.clients_lock:
            client = self.connected_clients.get(sid)
            return client and client['authenticated']

    def generate_socket_token(self, user_id: int) -> Dict[str, str]:
        """Generate JWT token for WebSocket"""
        expires = datetime.utcnow() + timedelta(hours=1)
        payload = {
            'user_id': user_id,
            'exp': expires,
            'purpose': 'socket_connection',
            'iss': 'media-server',
            'aud': 'socket-client',
            'iat': datetime.utcnow()
        }
        
        token = jwt.encode(
            payload,
            current_app.config['SECRET_KEY'],
            algorithm='HS256'
        )

        current_app.logger.info(f"Generated socket token for user {user_id}")
        return {
            'token': token,
            'expires_in': 3600,
            'expires_at': expires.isoformat()
        }

    def verify_socket_token(self, token: str) -> Dict:
        try:
            if not token:
                raise ValueError('Empty token provided')
            
            # Convert string to bytes if necessary
            if isinstance(token, str):
                token = token.encode('utf-8')
            
            decoded = jwt.decode(
                token, 
                current_app.config['SECRET_KEY'],
                algorithms=['HS256'],
                options={
                    'verify_exp': True,
                    'verify_aud': True,
                    'verify_iss': True,
                    'require': ['user_id', 'purpose']
                },
                audience='socket-client',
                issuer='media-server'
            )
            
            if decoded.get('purpose') != 'socket_connection':
                raise ValueError('Invalid token purpose')
                
            return decoded
            
        except jwt.ExpiredSignatureError:
            current_app.logger.warning("Expired socket token")
            raise ValueError('Token expired')
        except jwt.InvalidAudienceError:
            current_app.logger.warning("Invalid audience in socket token")
            raise ValueError('Invalid audience')
        except jwt.InvalidIssuerError:
            current_app.logger.warning("Invalid issuer in socket token")
            raise ValueError('Invalid issuer')
        except jwt.InvalidTokenError as e:
            current_app.logger.error(f"Invalid token: {str(e)}")
            raise ValueError(f'Invalid token: {str(e)}')
        except Exception as e:
            current_app.logger.error(f"Token verification failed: {str(e)}")
            raise ValueError(f'Token verification failed: {str(e)}')
