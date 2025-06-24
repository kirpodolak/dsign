# services/sockets/handlers/playlist.py
from typing import Dict, Optional, List, Any
from datetime import datetime
from flask_socketio import emit
from threading import Lock
from ...logger import ServiceLogger

class PlaylistHandler:
    """Handler for all playlist-related WebSocket operations"""

    def __init__(self, db_session, socket_service, logger: Optional[ServiceLogger] = None):
        self.db = db_session
        self.socket_service = socket_service
        self.logger = logger or ServiceLogger('PlaylistHandler')
        self.lock = Lock()

    def handle_get_playlist_state(self, data: Dict, sid: str) -> bool:
        """
        Handle request for playlist state
        Args:
            data: Dictionary containing 'playlist_id'
            sid: Client session ID
        Returns:
            bool: True if successful, False otherwise
        """
        from dsign.models import Playlist, PlaylistItem, PlaylistProfileAssignment, PlaybackProfile
        
        if not self.socket_service.is_authenticated(sid):
            self._emit_error(sid, 'playlist_error', 'Unauthorized access')
            return False

        try:
            playlist_id = data.get('playlist_id')
            if not playlist_id:
                raise ValueError("Playlist ID is required")

            with self.lock:
                playlist = self.db.session.query(Playlist).get(playlist_id)
                if not playlist:
                    raise ValueError(f"Playlist {playlist_id} not found")

                items = [{
                    'id': item.id,
                    'media_id': item.media_id,
                    'position': item.position,
                    'duration': item.duration,
                    'metadata': item.metadata
                } for item in playlist.items.order_by(PlaylistItem.position).all()]

                response = {
                    'id': playlist.id,
                    'name': playlist.name,
                    'customer': playlist.customer,
                    'items': items,
                    'timestamp': datetime.utcnow().isoformat()
                }

                emit('playlist_state', response, room=sid)
                self.logger.debug('Playlist state sent', {
                    'sid': sid,
                    'playlist_id': playlist_id,
                    'item_count': len(items)
                })
                return True

        except Exception as e:
            self._emit_error(sid, 'playlist_error', f"Failed to get playlist: {str(e)}")
            self.logger.error('Playlist state error', {
                'error': str(e),
                'sid': sid,
                'playlist_id': data.get('playlist_id'),
                'stack': True
            })
            return False

    def handle_profiles_request(self, data: Dict, sid: str) -> bool:
        """
        Handle request for available profiles
        Args:
            data: Request data (unused)
            sid: Client session ID
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.socket_service.is_authenticated(sid):
            self._emit_error(sid, 'profiles_error', 'Unauthorized access')
            return False

        try:
            with self.lock:
                profiles = self.db.session.query(PlaybackProfile).order_by(PlaybackProfile.name).all()
                
                response = {
                    'profiles': [{
                        'id': p.id,
                        'name': p.name,
                        'type': p.profile_type,
                        'description': p.description,
                        'settings': p.settings
                    } for p in profiles],
                    'timestamp': datetime.utcnow().isoformat()
                }

                emit('profiles_list', response, room=sid)
                self.logger.debug('Profiles list sent', {
                    'sid': sid,
                    'profile_count': len(profiles)
                })
                return True

        except Exception as e:
            self._emit_error(sid, 'profiles_error', f"Failed to get profiles: {str(e)}")
            self.logger.error('Profiles request error', {
                'error': str(e),
                'sid': sid,
                'stack': True
            })
            return False

    def handle_apply_profile(self, data: Dict, sid: str) -> bool:
        """
        Handle profile application to playlist
        Args:
            data: Dictionary containing 'playlist_id' and 'profile_id'
            sid: Client session ID
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.socket_service.is_authenticated(sid):
            self._emit_error(sid, 'profile_error', 'Unauthorized access')
            return False

        try:
            playlist_id = data.get('playlist_id')
            profile_id = data.get('profile_id')
            
            if not playlist_id or not profile_id:
                raise ValueError("Both playlist_id and profile_id are required")

            with self.lock:
                # Verify playlist exists
                playlist = self.db.session.query(Playlist).get(playlist_id)
                if not playlist:
                    raise ValueError(f"Playlist {playlist_id} not found")

                # Verify profile exists
                profile = self.db.session.query(PlaybackProfile).get(profile_id)
                if not profile:
                    raise ValueError(f"Profile {profile_id} not found")

                # Update or create assignment
                assignment = self.db.session.query(PlaylistProfileAssignment)\
                    .filter_by(playlist_id=playlist_id)\
                    .first()

                if assignment:
                    assignment.profile_id = profile_id
                else:
                    assignment = PlaylistProfileAssignment(
                        playlist_id=playlist_id,
                        profile_id=profile_id
                    )
                    self.db.session.add(assignment)

                self.db.session.commit()

                emit('profile_applied', {
                    'success': True,
                    'playlist_id': playlist_id,
                    'profile_id': profile_id,
                    'timestamp': datetime.utcnow().isoformat()
                }, room=sid)

                self.logger.info('Profile applied', {
                    'sid': sid,
                    'playlist_id': playlist_id,
                    'profile_id': profile_id
                })
                return True

        except Exception as e:
            self.db.session.rollback()
            self._emit_error(sid, 'profile_error', f"Failed to apply profile: {str(e)}")
            emit('profile_applied', {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }, room=sid)
            
            self.logger.error('Profile apply error', {
                'error': str(e),
                'sid': sid,
                'playlist_id': data.get('playlist_id'),
                'profile_id': data.get('profile_id'),
                'stack': True
            })
            return False

    def emit_playlist_update(self, playlist_id: int, update_type: str = 'full'):
        """
        Broadcast playlist update to all clients
        Args:
            playlist_id: ID of the updated playlist
            update_type: Type of update ('full' or 'partial')
        """
        try:
            with self.lock:
                playlist = self.db.session.query(Playlist).get(playlist_id)
                if not playlist:
                    self.logger.warning('Playlist not found for update', {
                        'playlist_id': playlist_id
                    })
                    return

                assignment = self.db.session.query(PlaylistProfileAssignment)\
                    .filter_by(playlist_id=playlist_id)\
                    .first()

                profile_data = None
                if assignment:
                    profile = self.db.session.query(PlaybackProfile).get(assignment.profile_id)
                    if profile:
                        profile_data = {
                            'id': profile.id,
                            'name': profile.name
                        }

                items = []
                if update_type == 'full':
                    items = [{
                        'id': item.id,
                        'media_id': item.media_id,
                        'position': item.position
                    } for item in playlist.items.order_by(PlaylistItem.position).all()]

                update_data = {
                    'playlist_id': playlist.id,
                    'name': playlist.name,
                    'customer': playlist.customer,
                    'update_type': update_type,
                    'profile': profile_data,
                    'items': items,
                    'timestamp': datetime.utcnow().isoformat()
                }

                emit('playlist_updated', update_data, broadcast=True)
                self.logger.debug('Playlist update broadcasted', {
                    'playlist_id': playlist_id,
                    'update_type': update_type,
                    'item_count': len(items)
                })

        except Exception as e:
            self.logger.error('Failed to broadcast playlist update', {
                'error': str(e),
                'playlist_id': playlist_id,
                'stack': True
            })

    def _emit_error(self, sid: str, event: str, message: str):
        """Helper method to emit error messages"""
        emit(event, {
            'message': message,
            'timestamp': datetime.utcnow().isoformat()
        }, room=sid)
