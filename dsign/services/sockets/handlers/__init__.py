# services/sockets/handlers/__init__.py
from typing import Dict, Any, Optional
from datetime import datetime
from flask_socketio import SocketIO, emit, Namespace
from threading import Lock
from ...logger import ServiceLogger
from .system import SystemHandlers, SystemNamespace
from .playlist import PlaylistHandler
from .playback import PlaybackHandler
from .auth import AuthHandler

class BaseHandler:
    """Base class for all socket handlers with common functionality"""
    
    def __init__(self, db_session, socketio: SocketIO, logger: Optional[ServiceLogger] = None):
        self.db = db_session
        self.socketio = socketio
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self.lock = Lock()
        
    def _check_authentication(self, sid: str) -> bool:
        """Check if client is authenticated (to be implemented by the main service)"""
        raise NotImplementedError("Authentication check should be implemented by the main service")

    def _emit_error(self, event: str, sid: str, error_msg: str):
        """Helper method to emit error messages"""
        emit(event, {
            'message': error_msg,
            'timestamp': datetime.utcnow().isoformat()
        }, room=sid)
        self.logger.error(error_msg, {'sid': sid, 'stack': True})

    def _emit_success(self, event: str, sid: str, data: Dict[str, Any]):
        """Helper method to emit success responses"""
        data['timestamp'] = datetime.utcnow().isoformat()
        emit(event, data, room=sid)
        self.logger.debug(f"{event} sent", {'sid': sid, 'data_keys': list(data.keys())})

__all__ = ['SystemHandlers', 'SystemNamespace', 'PlaylistHandler', 'PlaybackHandler', 'AuthHandler']
