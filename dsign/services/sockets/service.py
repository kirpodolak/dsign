# services/sockets/service.py
from typing import Optional, Dict, Any
from flask import current_app
from flask_socketio import SocketIO, Namespace
from threading import Lock
from datetime import datetime
import time
import traceback
from .handlers.system import SystemHandlers, SystemNamespace
from .handlers.playlist import PlaylistHandler
from .handlers.playback import PlaybackHandler
from ..logger import ServiceLogger

class SocketService:
    """Main WebSocket service managing all socket connections and events"""

    def __init__(self, socketio: SocketIO, db_session=None, app=None, logger: Optional[ServiceLogger] = None):
        """
        Initialize socket service
        Args:
            socketio: Flask-SocketIO instance
            db_session: Database session
            app: Flask application instance
            logger: Custom logger instance
        """
        if not socketio:
            raise ValueError("SocketIO instance is required")

        self.socketio = socketio
        self.db = db_session
        self.app = app or current_app._get_current_object() if current_app else None
        self.logger = logger or ServiceLogger('SocketService')
        self.server_version = "1.0.0"
        
        # Initialize handlers
        self.system = SystemHandlers(self)
        self.playlist = PlaylistHandler(db_session, self, self.logger)
        self.playback = PlaybackHandler(db_session, self, self.logger)

        # Setup configuration
        self._setup_config()
        self._register_handlers()
        self._start_background_tasks()

    def _setup_config(self):
        """Initialize service configuration"""
        self.config = {
            'max_retries': 5,
            'reconnect_attempts': 5,
            'reconnect_delay': 1000,
            'ping_interval': 25,
            'ping_timeout': 60
        }

    def _register_handlers(self):
        """Register all event handlers and namespaces"""
        # System events
        self.socketio.on_event('connect', self.system.setup_connection_handler)
        self.socketio.on_event('disconnect', self.system.handle_disconnect)
        self.socketio.on_event('ping', self.system.handle_ping)
        self.socketio.on_event('heartbeat', self.system.handle_heartbeat)
        self.socketio.on_event('get_server_version', self.system.handle_get_version)
        self.socketio.on_event('request_auth_status', self.system.handle_auth_status_request)

        # Playlist events
        self.socketio.on_event('get_playlist_state', self.playlist.handle_get_playlist_state)
        self.socketio.on_event('request_profiles', self.playlist.handle_profiles_request)
        self.socketio.on_event('apply_profile', self.playlist.handle_apply_profile)

        # Playback events
        self.socketio.on_event('get_playback_state', self.playback.handle_get_playback_state)
        self.socketio.on_event('playback_command', self.playback.handle_playback_command)

        # Register namespaces
        self.socketio.on_namespace(SystemNamespace('/system', self))

    def _start_background_tasks(self):
        """Start all required background tasks"""
        self.system.start_activity_checker()
        self.system.start_version_broadcaster()

    def init_app(self, app):
        """
        Initialize with Flask application
        Args:
            app: Flask application instance
        """
        self.app = app
        self.logger.info("SocketService initialized with Flask app")

    def emit_playlist_update(self, playlist_id: int, update_type: str = 'full'):
        """
        Broadcast playlist update to all clients
        Args:
            playlist_id: ID of updated playlist
            update_type: Type of update ('full' or 'partial')
        """
        self.playlist.emit_playlist_update(playlist_id, update_type)

    def emit_playback_update(self, state_data: Dict[str, Any]):
        """
        Broadcast playback state update
        Args:
            state_data: Playback state dictionary
        """
        self.playback.emit_playback_update(state_data)

    def generate_socket_token(self, user_id: int) -> Dict[str, str]:
        """
        Generate JWT token for socket authentication
        Args:
            user_id: User ID to include in token
        Returns:
            Dictionary containing token and expiration info
        """
        return self.system.generate_socket_token(user_id)

    def verify_socket_token(self, token: str) -> Dict:
        """
        Verify socket authentication token
        Args:
            token: JWT token to verify
        Returns:
            Decoded token payload
        Raises:
            ValueError: If token is invalid
        """
        return self.system.verify_socket_token(token)

    def is_authenticated(self, sid: str) -> bool:
        """
        Check if client is authenticated
        Args:
            sid: Session ID to check
        Returns:
            bool: True if authenticated, False otherwise
        """
        return self.system.is_authenticated(sid)

    def get_connected_clients(self) -> Dict[str, Dict]:
        """
        Get information about connected clients
        Returns:
            Dictionary of connected clients
        """
        return self.system.connected_clients

    def disconnect_client(self, sid: str):
        """
        Disconnect specific client
        Args:
            sid: Session ID to disconnect
        """
        self.system.cleanup_client(sid)

    def broadcast_auth_status(self, user_id: Optional[int] = None):
        """
        Broadcast authentication status update
        Args:
            user_id: Optional specific user ID to notify
        """
        with self.system.clients_lock:
            if user_id:
                target_sids = [sid for sid, client in self.system.connected_clients.items() 
                             if client.get('user_id') == user_id]
            else:
                target_sids = [sid for sid, client in self.system.connected_clients.items() 
                             if client.get('authenticated')]

        for sid in target_sids:
            self.system.handle_auth_status_request({}, sid=sid)

        self.logger.debug('Auth status broadcasted', {
            'user_id': user_id,
            'recipients': len(target_sids)
        })

    def handle_exception(self, error: Exception):
        """
        Handle uncaught exceptions
        Args:
            error: Exception instance
        """
        self.logger.error('Unhandled socket error', {
            'error': str(error),
            'stack_trace': str(traceback.format_exc())
        })
