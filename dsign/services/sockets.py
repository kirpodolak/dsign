from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from flask_socketio import SocketIO, disconnect
from flask import request, current_app
import jwt
import logging

class SocketService:
    def __init__(self, socketio: SocketIO, db_session, logger: Optional[logging.Logger] = None):
        """
        Инициализация сервиса сокетов
        :param socketio: Экземпляр SocketIO
        :param db_session: Сессия базы данных
        :param logger: Логгер (опционально)
        """
        self.socketio = socketio
        self.db = db_session
        self.logger = logger or logging.getLogger(__name__)
        self.connected_clients = {}

        # Регистрация обработчиков событий
        self.socketio.on_event('connect', self.handle_connect)
        self.socketio.on_event('disconnect', self.handle_disconnect)
        self.socketio.on_event('authenticate', self.handle_authentication)
        self.socketio.on_event('request_profiles', self.handle_profiles_request)
        self.socketio.on_event('apply_profile', self.handle_apply_profile)

    def generate_socket_token(self, user_id: int) -> str:
        """Генерация JWT токена для WebSocket аутентификации"""
        payload = {
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(minutes=5)
        }
        return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

    def verify_socket_token(self, token: str) -> Dict:
        """Верификация JWT токена"""
        try:
            return jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise ValueError('Token expired')
        except jwt.InvalidTokenError:
            raise ValueError('Invalid token')

    def handle_connect(self):
        """Обработчик подключения клиента"""
        self.logger.info(f'Client connecting from {request.remote_addr}')
        # Ожидаем аутентификацию в течение 5 секунд
        request.sid_auth_timeout = current_app.config.get('SOCKET_AUTH_TIMEOUT', 5)

    def handle_authentication(self, data: Dict):
        """Обработчик аутентификации WebSocket"""
        try:
            token = self.verify_socket_token(data.get('token'))
            user_id = token['user_id']
            
            # Проверяем существование пользователя в БД
            from ..models import User
            user = self.db.session.query(User).get(user_id)
            if not user:
                raise ValueError('User not found')
            
            # Сохраняем информацию о клиенте
            self.connected_clients[request.sid] = {
                'user_id': user_id,
                'authenticated': True,
                'last_activity': datetime.utcnow()
            }
            
            self.logger.info(f'User {user_id} authenticated via WebSocket')
            self.socketio.emit('authentication_result', {'success': True}, room=request.sid)
            
        except Exception as e:
            self.logger.error(f'WebSocket authentication failed: {str(e)}')
            self.socketio.emit('authentication_result', {
                'success': False,
                'error': str(e)
            }, room=request.sid)
            disconnect()

    def handle_disconnect(self):
        """Обработчик отключения клиента"""
        if request.sid in self.connected_clients:
            user_id = self.connected_clients[request.sid]['user_id']
            self.logger.info(f'User {user_id} disconnected')
            del self.connected_clients[request.sid]
        else:
            self.logger.info('Unauthenticated client disconnected')

    def is_authenticated(self, sid: str) -> bool:
        """Проверка аутентификации клиента"""
        client = self.connected_clients.get(sid)
        return client and client['authenticated']

    def emit_playlist_update(self, playlist_id: Optional[int] = None):
        """
        Отправка обновления плейлиста
        :param playlist_id: ID активного плейлиста
        """
        from ..models import Playlist, PlaylistProfileAssignment, PlaybackProfile

        data: Dict[str, Any] = {'active_playlist': None}
        
        if playlist_id:
            playlist = self.db.session.query(Playlist).get(playlist_id)
            if playlist:
                data['active_playlist'] = {
                    'id': playlist.id,
                    'name': playlist.name,
                    'customer': playlist.customer or "N/A"
                }
                
                # Добавляем информацию о профиле
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
        
        # Отправляем только аутентифицированным клиентам
        for sid in list(self.connected_clients.keys()):
            if self.is_authenticated(sid):
                self.socketio.emit('playlist_update', data, room=sid)
        
    def handle_profiles_request(self, data: Dict):
        """Отправка списка профилей клиенту"""
        if not self.is_authenticated(request.sid):
            disconnect()
            return

        from ..models import PlaybackProfile
        profiles = self.db.session.query(PlaybackProfile).all()
        self.socketio.emit('profiles_list', {
            'profiles': [{
                'id': p.id,
                'name': p.name,
                'type': p.profile_type
            } for p in profiles]
        }, room=request.sid)

    def handle_apply_profile(self, data: Dict):
        """Применение профиля настроек"""
        if not self.is_authenticated(request.sid):
            disconnect()
            return

        profile_id = data.get('profile_id')
        playlist_id = data.get('playlist_id')
    
        if profile_id and playlist_id:
            from ..models import PlaylistProfileAssignment
            # Назначаем профиль плейлисту
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
            self.socketio.emit('profile_applied', {'success': True}, room=request.sid)

    def check_auth_timeouts(self):
        """Проверка таймаутов аутентификации"""
        timeout = current_app.config.get('SOCKET_AUTH_TIMEOUT', 5)
        now = datetime.utcnow()
        
        for sid, info in list(self.connected_clients.items()):
            if not info['authenticated'] and (now - info['connect_time']).seconds > timeout:
                self.logger.warning(f'Disconnecting client {sid} due to auth timeout')
                self.socketio.emit('auth_timeout', {}, room=sid)
                disconnect(sid)
                del self.connected_clients[sid]
