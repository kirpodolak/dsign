from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from flask_socketio import SocketIO, disconnect, Namespace, emit
from flask import request, current_app
import jwt
from .logger import ServiceLogger
from threading import Lock
import time

class AuthNamespace(Namespace):
    """
    Специальный namespace для обработки аутентификации
    """
    def __init__(self, namespace, socket_service):
        super().__init__(namespace)
        self.socket_service = socket_service
        self.logger = socket_service.logger

    def on_connect(self):
        """Обработчик подключения к namespace /auth"""
        self.logger.debug('New auth namespace connection', {
            'sid': request.sid,
            'ip': request.remote_addr
        })
        
        # Отправляем клиенту подтверждение готовности
        self.emit('auth_ready', {
            'version': self.socket_service.server_version,
            'timestamp': datetime.utcnow().isoformat()
        })

    def on_authenticate(self, data):
        """Обработчик аутентификации в namespace /auth"""
        try:
            token = data.get('token')
            if not token:
                raise ValueError('Token is required')
            
            # Верифицируем токен
            decoded = self.socket_service.verify_socket_token(token)
            user_id = decoded['user_id']
            
            # Обновляем статус клиента
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

class SocketService:
    def __init__(self, socketio: Optional[SocketIO] = None, db_session=None, app=None, logger=None):
        """
        Инициализация сервиса сокетов
        :param socketio: Экземпляр SocketIO (обязательно)
        :param db_session: Сессия базы данных
        :param app: Экземпляр Flask приложения
        :param logger: Логгер
        """
        if socketio is None:
            raise ValueError("SocketIO instance is required")
            
        self.socketio = socketio
        self.db = db_session
        self.app = app or current_app._get_current_object() if current_app else None
        self.logger = logger or ServiceLogger('SocketService')
        self.connected_clients = {}
        self.clients_lock = Lock()
        self.activity_check_interval = 15  # seconds
        self.socket_auth_timeout = 30  # 30 seconds timeout to match frontend
        self.server_version = "1.0.0"
        
        # Конфигурация, соответствующая фронтенду
        self.config = {
            'max_retries': 5,
            'reconnect_attempts': 5,
            'reconnect_delay': 1000,
            'ping_interval': 25  # seconds to match frontend
        }

        # Регистрируем кастомный namespace для аутентификации
        self.socketio.on_namespace(AuthNamespace('/auth', self))
        
        self.setup_handlers()
        self.register_handlers()
        self.start_activity_checker()
        self.start_version_broadcaster()

    def setup_handlers(self):
        """Настройка обработчиков событий"""
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            """Обработчик подключения WebSocket"""
            try:
                token = (
                    request.args.get('token') or 
                    (auth.get('token') if auth else None) or
                    request.headers.get('Authorization', '').replace('Bearer ', '')
                )

                if not token:
                    emit('auth_required', {
                        'message': 'Token required',
                        'timestamp': datetime.utcnow().isoformat()
                    })
                    return False

                # Проверка токена
                decoded = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
                
                if decoded.get('purpose') != 'socket_connection':
                    raise ValueError('Invalid token purpose')
                
                # Сохраняем информацию о клиенте
                with self.clients_lock:
                    self.connected_clients[request.sid] = {
                        'user_id': decoded['user_id'],
                        'connected_at': datetime.utcnow(),
                        'last_activity': datetime.utcnow(),
                        'authenticated': True,
                        'namespace': request.namespace
                    }
                
                self.logger.info('Client connected', {
                    'sid': request.sid,
                    'user_id': decoded['user_id'],
                    'namespace': request.namespace
                })
                
                return True

            except jwt.ExpiredSignatureError:
                emit('token_expired', {
                    'message': 'Token expired',
                    'timestamp': datetime.utcnow().isoformat()
                })
                return False
            except Exception as e:
                self.logger.error(f'Auth error: {str(e)}', {
                    'sid': request.sid,
                    'error': str(e)
                })
                emit('auth_error', {
                    'message': str(e),
                    'timestamp': datetime.utcnow().isoformat()
                })
                return False

    def init_app(self, app):
        """Инициализация с Flask приложением (для обратной совместимости)"""
        self.app = app
        self.logger.info("SocketService initialized with Flask app")

    def register_handlers(self):
        """Регистрация всех обработчиков событий для основного namespace"""
        self.socketio.on_event('disconnect', self.handle_disconnect)
        self.socketio.on_event('authenticate', self.handle_authentication)
        self.socketio.on_event('request_profiles', self.handle_profiles_request)
        self.socketio.on_event('apply_profile', self.handle_apply_profile)
        self.socketio.on_event('ping', self.handle_ping)
        self.socketio.on_event('heartbeat', self.handle_heartbeat)
        self.socketio.on_event('get_server_version', self.handle_get_version)
        self.socketio.on_event('get_playback_state', self.handle_get_playback_state)
        self.socketio.on_event('get_playlist_state', self.handle_get_playlist_state)
        self.socketio.on_event('request_auth_status', self.handle_auth_status_request)

    def start_activity_checker(self):
        """Запуск фоновой проверки активности клиентов"""
        def activity_check():
            while True:
                self.socketio.sleep(self.activity_check_interval)
                try:
                    self.check_activity()
                except Exception as e:
                    self.logger.error('Activity check failed', {'error': str(e)})

        self.socketio.start_background_task(activity_check)

    def start_version_broadcaster(self):
        """Периодическая рассылка версии сервера"""
        def broadcast_version():
            while True:
                self.socketio.sleep(300)  # Каждые 5 минут
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
        """Обработчик запроса статуса аутентификации"""
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
        """Широковещательная рассылка об изменении статуса аутентификации"""
        with self.clients_lock:
            if user_id:
                # Отправляем только конкретному пользователю
                target_sids = [sid for sid, client in self.connected_clients.items() 
                              if client.get('user_id') == user_id]
            else:
                # Отправляем всем аутентифицированным клиентам
                target_sids = [sid for sid, client in self.connected_clients.items() 
                              if client.get('authenticated')]

        for sid in target_sids:
            self.handle_auth_status_request({}, sid=sid)

        self.logger.debug('Broadcast auth status', {
            'user_id': user_id,
            'recipients': len(target_sids)
        })

    def handle_disconnect(self):
        """Обработчик отключения клиента"""
        sid = request.sid
        self.logger.info('Client disconnected', {
            'sid': sid,
            'namespace': request.namespace
        })
        self.cleanup_client(sid)

    def handle_ping(self, data=None):
        """Обработчик ping-сообщения"""
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
        """Обработчик heartbeat сообщения"""
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
        """Обработчик запроса версии сервера"""
        sid = request.sid
        self.socketio.emit('server_version', {
            'version': self.server_version,
            'timestamp': datetime.utcnow().isoformat()
        }, room=sid)

    def handle_authentication(self, data: Dict):
        """Обработчик аутентификации WebSocket в основном namespace"""
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
            
            # Отправляем обновленный статус после аутентификации
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
        """Очистка ресурсов клиента"""
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
        """Проверка активности клиентов"""
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
        """Проверка аутентификации клиента"""
        with self.clients_lock:
            client = self.connected_clients.get(sid)
            return client and client['authenticated']

    def handle_get_playback_state(self, data: Dict):
        """Обработчик запроса состояния воспроизведения"""
        sid = request.sid
        if not self.is_authenticated(sid):
            self.logger.warning('Unauthorized playback state request', {'sid': sid})
            disconnect(sid)
            return

        try:
            from ..models import PlaybackState
            state = self.db.session.query(PlaybackState).first()
            if state:
                self.socketio.emit('playback_update', {
                    'state': state.state,
                    'current_time': state.current_time,
                    'playlist_item_id': state.playlist_item_id,
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)
        except Exception as e:
            self.logger.error('Failed to get playback state', {
                'error': str(e),
                'sid': sid
            })

    def handle_get_playlist_state(self, data: Dict):
        """Обработчик запроса состояния плейлиста"""
        sid = request.sid
        if not self.is_authenticated(sid):
            self.logger.warning('Unauthorized playlist state request', {'sid': sid})
            disconnect(sid)
            return

        try:
            from ..models import Playlist
            playlist = self.db.session.query(Playlist).get(data.get('playlist_id'))
            if playlist:
                self.socketio.emit('playlist_update', {
                    'id': playlist.id,
                    'name': playlist.name,
                    'items': [item.to_dict() for item in playlist.items],
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)
        except Exception as e:
            self.logger.error('Failed to get playlist state', {
                'error': str(e),
                'sid': sid,
                'playlist_id': data.get('playlist_id')
            })

    def emit_playlist_update(self, playlist_id: Optional[int] = None):
        """Отправка обновления плейлиста"""
        from ..models import Playlist, PlaylistProfileAssignment, PlaybackProfile

        data: Dict[str, Any] = {'active_playlist': None}
        
        try:
            if playlist_id:
                playlist = self.db.session.query(Playlist).get(playlist_id)
                if playlist:
                    data['active_playlist'] = {
                        'id': playlist.id,
                        'name': playlist.name,
                        'customer': playlist.customer or "N/A"
                    }
                    
                    assignment = self.db.session.query(PlaylistProfileAssignment).filter_by(
                        playlist_id=playlist_id
                    ).first()
                
                    if assignment:
                        profile = self.db.session.query(PlaybackProfile).get(assignment.profile_id)
                        if profile:
                            data['assigned_profile'] = {
                                'id': profile.id,
                                'name': profile.name
                            }
            
            with self.clients_lock:
                active_sids = [sid for sid in self.connected_clients 
                             if self.is_authenticated(sid)]
            
            data['timestamp'] = datetime.utcnow().isoformat()
            
            for sid in active_sids:
                try:
                    self.socketio.emit('playlist_update', data, room=sid)
                except Exception as e:
                    self.logger.error('Failed to send update', {
                        'sid': sid,
                        'error': str(e)
                    })
                    self.cleanup_client(sid)
                    
            self.logger.debug('Playlist update sent', {
                'playlist_id': playlist_id,
                'recipients_count': len(active_sids)
            })
            
        except Exception as e:
            self.logger.error('Failed to prepare playlist update', {
                'error': str(e),
                'playlist_id': playlist_id
            })

    def handle_profiles_request(self, data: Dict):
        """Отправка списка профилей клиенту"""
        sid = request.sid
        if not self.is_authenticated(sid):
            self.logger.warning('Unauthorized profiles request', {'sid': sid})
            disconnect(sid)
            return

        try:
            from ..models import PlaybackProfile
            profiles = self.db.session.query(PlaybackProfile).all()
            self.socketio.emit('profiles_list', {
                'profiles': [{
                    'id': p.id,
                    'name': p.name,
                    'type': p.profile_type
                } for p in profiles],
                'timestamp': datetime.utcnow().isoformat()
            }, room=sid)
            
            self.logger.debug('Profiles list sent', {'sid': sid})
            
        except Exception as e:
            self.logger.error('Failed to send profiles', {
                'sid': sid,
                'error': str(e)
            })
            self.cleanup_client(sid)

    def handle_apply_profile(self, data: Dict):
        """Применение профиля настроек"""
        sid = request.sid
        if not self.is_authenticated(sid):
            self.logger.warning('Unauthorized profile apply attempt', {'sid': sid})
            disconnect(sid)
            return

        profile_id = data.get('profile_id')
        playlist_id = data.get('playlist_id')
    
        try:
            if profile_id and playlist_id:
                from ..models import PlaylistProfileAssignment
                assignment = self.db.session.query(PlaylistProfileAssignment).filter_by(
                    playlist_id=playlist_id
                ).first()
            
                if assignment:
                    assignment.profile_id = profile_id
                else:
                    assignment = PlaylistProfileAssignment(
                        playlist_id=playlist_id,
                        profile_id=profile_id
                    )
                    self.db.session.add(assignment)
                
                self.db.session.commit()
                self.socketio.emit('profile_applied', {
                    'success': True,
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)
                self.logger.info('Profile applied', {
                    'profile_id': profile_id,
                    'playlist_id': playlist_id,
                    'sid': sid
                })
                
        except Exception as e:
            self.logger.error('Failed to apply profile', {
                'error': str(e),
                'profile_id': profile_id,
                'playlist_id': playlist_id
            })
            self.socketio.emit('profile_applied', {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }, room=sid)

    def generate_socket_token(self, user_id: int) -> Dict[str, str]:
        """Генерация JWT токена для WebSocket"""
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
        """Верификация JWT токена с полной проверкой claims"""
        try:
            if not token:
                raise ValueError('Empty token provided')
                
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
        except jwt.InvalidTokenError as e:
            current_app.logger.error(f"Invalid token: {str(e)}")
            raise ValueError(f'Invalid token: {str(e)}')
        except Exception as e:
            current_app.logger.error(f"Token verification failed: {str(e)}")
            raise ValueError(f'Token verification failed: {str(e)}')
