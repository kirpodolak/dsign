import os
import json
import time
from typing import Dict, Optional
from pathlib import Path

from .playback_constants import PlaybackConstants
from .playback_utils import PlaybackUtils

class PlaylistManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager, logo_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self._mpv_manager = mpv_manager
        self._logo_manager = logo_manager
        self._last_playback_state = {}
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)

    def play(self, playlist_id: int) -> bool:
        """Play playlist with profile support"""
        # Исправленные импорты - конкретные классы из модулей
        try:
            from ..models.playlist import Playlist
            from ..models.playback_status import PlaybackStatus
            from ..models.playlist_profile_assignment import PlaylistProfileAssignment
            from ..models.playback_profile import PlaybackProfile
        except ImportError:
            # Альтернативные варианты импортов
            try:
                from dsign.models.playlist import Playlist
                from dsign.models.playback_status import PlaybackStatus
                from dsign.models.playlist_profile_assignment import PlaylistProfileAssignment
                from dsign.models.playback_profile import PlaybackProfile
            except ImportError:
                # Фолбэк на общий импорт с конкретными классами
                from ..models import (
                    Playlist as PlaylistModel,
                    PlaybackStatus as PlaybackStatusModel,
                    PlaylistProfileAssignment as PlaylistProfileAssignmentModel,
                    PlaybackProfile as PlaybackProfileModel
                )
                Playlist = PlaylistModel
                PlaybackStatus = PlaybackStatusModel
                PlaylistProfileAssignment = PlaylistProfileAssignmentModel
                PlaybackProfile = PlaybackProfileModel
    
        try:
            # Get playlist and validate
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            # Get assigned profile if exists
            profile_settings = {}
            assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=playlist_id
            ).first()
        
            if assignment and assignment.profile_id:
                profile = self.db_session.query(PlaybackProfile).get(assignment.profile_id)
                if profile:
                    profile_settings = json.loads(profile.settings)

            # Update playback status in DB
            playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
            playback.playlist_id = playlist_id
            playback.status = 'playing'
            self.db_session.add(playback)
            self.db_session.commit()

            # Apply profile settings first
            if profile_settings:
                if not self._mpv_manager.update_settings(profile_settings):
                    self.logger.warning("Failed to apply some profile settings")

            # Create playlist file and load it
            playlist_file = PlaybackUtils.create_playlist_file(
                self.upload_folder, 
                self.tmp_dir, 
                playlist
            )
        
            # Load playlist with retry logic
            result = self._mpv_manager._send_command({
                "command": ["loadlist", str(playlist_file), "replace"],
                "request_id": int(time.time() * 1000)
            }, timeout=10.0)
        
            if not result or result.get("error") != "success":
                raise RuntimeError("Failed to load playlist")

            # Ensure looping is enabled
            self._mpv_manager._send_command({
                "command": ["set_property", "loop-playlist", "inf"],
                "request_id": int(time.time() * 1000)
            })

            # Start playback
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"],
                "request_id": int(time.time() * 1000)
            })

            # Notify clients
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist': {'id': playlist.id, 'name': playlist.name},
                'settings': profile_settings
            })
        
            return True

        except Exception as e:
            self.logger.error(f"Playback error: {str(e)}")
            try:
                # Безопасный rollback
                if hasattr(self.db_session, 'rollback'):
                    self.db_session.rollback()
            except Exception as rollback_error:
                self.logger.error(f"Rollback failed: {str(rollback_error)}")
            
            # Fall back to idle logo
            try:
                self._logo_manager.display_idle_logo()
            except Exception as logo_error:
                self.logger.error(f"Failed to display idle logo: {str(logo_error)}")
            
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(self) -> bool:
        """Stop playback"""
        try:
            return self._logo_manager.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Stop error: {str(e)}")
            return False

    def get_status(self) -> Dict:
        """Get current playback status"""
        try:
            from ..models.playback_status import PlaybackStatus
        except ImportError:
            try:
                from dsign.models.playback_status import PlaybackStatus
            except ImportError:
                from ..models import PlaybackStatus as PlaybackStatusModel
                PlaybackStatus = PlaybackStatusModel
        
        status = self.db_session.query(PlaybackStatus).first()
        return {
            'status': status.status if status else None,
            'playlist_id': status.playlist_id if status else None,
            'settings': self._mpv_manager._current_settings
        }

    def restart_mpv(self) -> bool:
        """Restart MPV process with enhanced reliability"""
        try:
            if self._mpv_manager._mpv_process:
                try:
                    self._mpv_manager._mpv_process.terminate()
                    self._mpv_manager._mpv_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.logger.warning("MPV process did not terminate gracefully, killing...")
                    self._mpv_manager._mpv_process.kill()
                    self._mpv_manager._mpv_process.wait()
                except Exception as e:
                    self.logger.warning(f"Error terminating MPV process: {str(e)}")
            
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    os.unlink(PlaybackConstants.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Error removing socket: {str(e)}")
                    try:
                        os.chmod(PlaybackConstants.SOCKET_PATH, 0o777)
                        os.unlink(PlaybackConstants.SOCKET_PATH)
                    except:
                        pass
            
            self._mpv_manager._mpv_ready = False
            self._mpv_manager._socket_ready_event.clear()
            self._mpv_manager._ensure_mpv_service()
            
            try:
                self._mpv_manager._wait_for_mpv_ready(timeout=30)
                return True
            except Exception as e:
                self.logger.error(f"Failed to verify MPV restart: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(f"MPV restart failed: {str(e)}")
            return False

    def get_playback_info(self) -> Dict:
        """Get current playback info"""
        info = {}
        for category, settings in self._mpv_manager._current_settings.items():
            info[category] = {}
            for setting in settings.keys():
                response = self._mpv_manager._send_command({
                    "command": ["get_property", setting],
                    "request_id": int(time.time() * 1000)
                })
                if response and 'data' in response:
                    info[category][setting] = response['data']
        return info
        
    def stop_idle_logo(self):
        """Stop idle logo display"""
        try:
            res = self._mpv_manager._send_command({
                "command": ["stop"],
                "request_id": int(time.time() * 1000)
            })
            if res is not None:
                self.logger.info("Idle logo stopped")
            else:
                self.logger.warning("Failed to get confirmation of idle logo stop")
        except Exception as e:
            self.logger.error(f"Failed to stop idle logo: {str(e)}")
            
    def restart_idle_logo(self) -> bool:
        """Для обратной совместимости - вызывает основной метод без параметров"""
        return self._logo_manager.restart_idle_logo()
