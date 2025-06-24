# services/sockets/handlers/system.py
from typing import Dict, Optional, Any, List
from datetime import datetime, timedelta
from flask_socketio import SocketIO, disconnect, Namespace, emit
from flask import request, current_app
import jwt
import traceback
import time
from threading import Lock
from ...logger import ServiceLogger

class SystemNamespace(Namespace):
    """Namespace for handling system-level socket events"""
    
    def __init__(self, namespace, socket_service):
        super().__init__(namespace)
        self.socket_service = socket_service
        self.logger = socket_service.logger

    def on_connect(self):
        """Handle new connection to system namespace"""
        self.logger.debug('System namespace connection established', {
            'sid': request.sid,
            'ip': request.remote_addr,
            'namespace': request.namespace
        })
        
        emit('system_ready', {
            'version': self.socket_service.server_version,
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
                
            decoded = self.socket_service.verify_socket_token(token)
            user_id = decoded['user_id']
            
            with self.socket_service.clients_lock:
                if request.sid in self.socket_service.connected_clients:
                    self.socket_service.connected_clients[request.sid].update({
                        'user_id': user_id,
                        'authenticated': True,
                        'last_activity': datetime.utcnow()
                    })
                else:
                    self.logger.warning('Unknown client attempted authentication', {
                        'sid': request.sid
                    })
                    disconnect(request.sid)
                    return
                    
            self.logger.info('Client authenticated successfully', {
                'user_id': user_id,
                'sid': request.sid
            })
            
            emit('authentication_result', {
                'success': True,
                'user_id': user_id,
                'version': self.socket_service.server_version,
                'timestamp': datetime.utcnow().isoformat()
            })
            
        except jwt.ExpiredSignatureError:
            self._handle_auth_error('Token expired', request.sid)
        except jwt.InvalidTokenError as e:
            self._handle_auth_error(f'Invalid token: {str(e)}', request.sid)
        except Exception as e:
            self._handle_auth_error(f'Authentication failed: {str(e)}', request.sid)

    def _handle_auth_error(self, error_msg: str, sid: str):
        """Helper method for handling authentication errors"""
        self.logger.error('Authentication failed', {
            'error': error_msg,
            'sid': sid
        })
        
        emit('authentication_result', {
            'success': False,
            'error': error_msg,
            'timestamp': datetime.utcnow().isoformat()
        }, room=sid)
        
        if 'token' in error_msg.lower():
            emit('auth_error', {
                'message': error_msg,
                'timestamp': datetime.utcnow().isoformat()
            }, room=sid)
            
        disconnect(sid)

    def on_ping(self, data=None):
        """Handle ping request"""
        return self.socket_service.handle_ping(data)

    def on_heartbeat(self, data):
        """Handle heartbeat request"""
        return self.socket_service.handle_heartbeat(data)

    def on_get_server_version(self):
        """Handle version request"""
        self.socket_service.handle_get_version()

    def on_request_auth_status(self, data):
        """Handle authentication status request"""
        self.socket_service.handle_auth_status_request(data)

class SystemHandlers:
    """Main system event handlers"""
    
    def __init__(self, socket_service):
        self.socket_service = socket_service
        self.logger = socket_service.logger
        self.server_version = "1.0.0"
        self.activity_check_interval = 15  # seconds
        self.socket_auth_timeout = 30  # seconds
        self.clients_lock = Lock()
        self.connected_clients = {}

    def setup_connection_handler(self):
        """Setup global connection handler"""
        @self.socket_service.socketio.on('connect')
        def handle_connect(auth=None):
            try:
                token = self._extract_token(auth)
                if not token:
                    return self._handle_missing_token()
                    
                decoded = self.verify_socket_token(token)
                self._register_client(decoded)
                
                emit('connection_ack', {
                    'status': 'authenticated',
                    'user_id': decoded['user_id'],
                    'timestamp': datetime.utcnow().isoformat()
                })
                return True
                
            except jwt.ExpiredSignatureError:
                return self._handle_token_error('Token expired', 'token_expired')
            except jwt.InvalidTokenError as e:
                return self._handle_token_error(f'Invalid token: {str(e)}', 'auth_error')
            except Exception as e:
                return self._handle_connection_error(str(e))

    def _extract_token(self, auth) -> Optional[str]:
        """Extract token from various sources"""
        token = (
            request.args.get('token') or 
            (auth.get('token') if auth and isinstance(auth, dict) else None) or
            request.headers.get('Authorization', '').replace('Bearer ', '')
        )
        
        if not token:
            return None
            
        if isinstance(token, dict):
            token = token.get('token', '')
        if isinstance(token, bytes):
            token = token.decode('utf-8')
            
        if not isinstance(token, str) or not token.strip():
            raise ValueError('Invalid token format')
            
        return token

    def _handle_missing_token(self):
        """Handle missing token case"""
        emit('auth_required', {
            'message': 'Authentication token required',
            'timestamp': datetime.utcnow().isoformat()
        })
        return False

    def _handle_token_error(self, error_msg: str, error_event: str):
        """Handle token validation errors"""
        self.logger.warning('Connection rejected', {
            'reason': error_msg,
            'sid': request.sid,
            'ip': request.remote_addr
        })
        
        emit(error_event, {
            'message': error_msg,
            'timestamp': datetime.utcnow().isoformat()
        })
        return False

    def _handle_connection_error(self, error_msg: str):
        """Handle general connection errors"""
        self.logger.error('Connection failed', {
            'error': error_msg,
            'sid': request.sid,
            'ip': request.remote_addr,
            'stack_trace': traceback.format_exc()
        })
        
        emit('connection_error', {
            'message': f'Connection error: {error_msg}',
            'timestamp': datetime.utcnow().isoformat()
        })
        return False

    def _register_client(self, decoded: Dict):
        """Register new authenticated client"""
        with self.clients_lock:
            self.connected_clients[request.sid] = {
                'user_id': decoded['user_id'],
                'connected_at': datetime.utcnow(),
                'last_activity': datetime.utcnow(),
                'authenticated': True,
                'namespace': request.namespace,
                'ip': request.remote_addr
            }
            
        self.logger.info('New client connected', {
            'sid': request.sid,
            'user_id': decoded['user_id'],
            'namespace': request.namespace,
            'ip': request.remote_addr
        })

    def handle_disconnect(self):
        """Handle client disconnection"""
        sid = request.sid
        self.logger.info('Client disconnected', {
            'sid': sid,
            'namespace': request.namespace
        })
        self.cleanup_client(sid)

    def cleanup_client(self, sid: str):
        """Clean up client resources"""
        with self.clients_lock:
            if sid in self.connected_clients:
                client_info = self.connected_clients[sid]
                self.logger.debug('Cleaning up client', {
                    'sid': sid,
                    'user_id': client_info.get('user_id'),
                    'duration': (datetime.utcnow() - client_info['connected_at']).total_seconds()
                })
                del self.connected_clients[sid]
                disconnect(sid)

    def handle_ping(self, data=None):
        """Handle ping request"""
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
        """Handle heartbeat with latency calculation"""
        sid = request.sid
        timestamp = data.get('timestamp')
        
        with self.clients_lock:
            if sid in self.connected_clients:
                self.connected_clients[sid]['last_activity'] = datetime.utcnow()
                self.logger.debug('Heartbeat received', {'sid': sid})
                
                if timestamp:
                    try:
                        latency = int((time.time() * 1000) - float(timestamp))
                        return {
                            'latency': latency,
                            'timestamp': datetime.utcnow().isoformat()
                        }
                    except (ValueError, TypeError):
                        pass
                        
        return {
            'error': 'Invalid heartbeat data',
            'timestamp': datetime.utcnow().isoformat()
        }

    def handle_get_version(self):
        """Handle version request"""
        sid = request.sid
        self.socket_service.socketio.emit('server_version', {
            'version': self.server_version,
            'timestamp': datetime.utcnow().isoformat()
        }, room=sid)

    def handle_auth_status_request(self, data: Dict, sid: Optional[str] = None):
        """Handle authentication status request"""
        sid = sid or request.sid
        with self.clients_lock:
            client = self.connected_clients.get(sid)
            if client:
                self.socket_service.socketio.emit('auth_status_response', {
                    'authenticated': client.get('authenticated', False),
                    'user_id': client.get('user_id'),
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)

    def start_activity_checker(self):
        """Start background activity checker"""
        def activity_check():
            while True:
                self.socket_service.socketio.sleep(self.activity_check_interval)
                try:
                    self._check_inactive_clients()
                except Exception as e:
                    self.logger.error('Activity check failed', {'error': str(e)})

        self.socket_service.socketio.start_background_task(activity_check)

    def _check_inactive_clients(self):
        """Check for and disconnect inactive clients"""
        now = datetime.utcnow()
        to_disconnect = []
        
        with self.clients_lock:
            for sid, client in self.connected_clients.items():
                inactive_time = (now - client['last_activity']).total_seconds()
                
                if not client['authenticated']:
                    # Check authentication timeout
                    if (now - client['connected_at']).total_seconds() > self.socket_auth_timeout:
                        to_disconnect.append((sid, 'Authentication timeout'))
                elif inactive_time > 60:  # 1 minute inactivity timeout
                    to_disconnect.append((sid, f'Inactive for {inactive_time:.0f} seconds'))
                    
        for sid, reason in to_disconnect:
            self.logger.warning('Disconnecting client', {'sid': sid, 'reason': reason})
            self.socket_service.socketio.emit('inactivity_timeout', {
                'message': reason,
                'timestamp': now.isoformat()
            }, room=sid)
            self.cleanup_client(sid)

    def start_version_broadcaster(self):
        """Start periodic version broadcasting"""
        def broadcast():
            while True:
                self.socket_service.socketio.sleep(300)  # 5 minutes
                try:
                    with self.clients_lock:
                        active_sids = [sid for sid, client in self.connected_clients.items() 
                                     if client.get('authenticated')]
                    
                    for sid in active_sids:
                        self.socket_service.socketio.emit('server_version', {
                            'version': self.server_version,
                            'timestamp': datetime.utcnow().isoformat()
                        }, room=sid)
                except Exception as e:
                    self.logger.error('Version broadcast failed', {'error': str(e)})

        self.socket_service.socketio.start_background_task(broadcast)

    def generate_socket_token(self, user_id: int) -> Dict[str, str]:
        """Generate JWT token for socket connections"""
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
        
        self.logger.info('Generated socket token', {'user_id': user_id})
        return {
            'token': token,
            'expires_in': 3600,
            'expires_at': expires.isoformat()
        }

    def verify_socket_token(self, token: str) -> Dict:
        """Verify JWT token for socket connections"""
        try:
            if not token:
                raise ValueError('Empty token provided')
                
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
            self.logger.warning('Expired token encountered')
            raise ValueError('Token expired')
        except jwt.InvalidAudienceError:
            self.logger.warning('Invalid token audience')
            raise ValueError('Invalid audience')
        except jwt.InvalidIssuerError:
            self.logger.warning('Invalid token issuer')
            raise ValueError('Invalid issuer')
        except jwt.InvalidTokenError as e:
            self.logger.error(f'Invalid token: {str(e)}')
            raise ValueError(f'Invalid token: {str(e)}')
        except Exception as e:
            self.logger.error(f'Token verification failed: {str(e)}')
            raise ValueError(f'Token verification failed: {str(e)}')

    def is_authenticated(self, sid: str) -> bool:
        """Check if client is authenticated"""
        with self.clients_lock:
            client = self.connected_clients.get(sid)
            return client and client.get('authenticated', False)
