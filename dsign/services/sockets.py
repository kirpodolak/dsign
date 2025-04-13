from typing import Optional, Dict, Any
from flask_socketio import SocketIO
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

        # Регистрация обработчиков событий
        self.socketio.on_event('connect', self.handle_connect)
        self.socketio.on_event('disconnect', self.handle_disconnect)
        self.socketio.on_event('request_profiles', self.handle_profiles_request)
        self.socketio.on_event('apply_profile', self.handle_apply_profile)
    
    def handle_connect(self):
        """Обработчик подключения клиента"""
        self.logger.info('Client connected via WebSocket')

    def handle_disconnect(self):
        """Обработчик отключения клиента"""
        self.logger.info('Client disconnected')

    def emit_playlist_update(self, playlist_id: Optional[int] = None):
        """
        Отправка обновления плейлиста
        :param playlist_id: ID активного плейлиста
        """
        from ..models import Playlist  # Локальный импорт

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
        
        self.socketio.emit('playlist_update', data)
        
    def handle_profiles_request(self):
        """Отправка списка профилей клиенту"""
        from ..models import PlaybackProfile
        profiles = self.db.session.query(PlaybackProfile).all()
        self.socketio.emit('profiles_list', {
            'profiles': [{
                'id': p.id,
                'name': p.name,
                'type': p.profile_type
            } for p in profiles]
        })

    def handle_apply_profile(self, data):
        """Применение профиля настроек"""
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
        self.socketio.emit('profile_applied', {'success': True})