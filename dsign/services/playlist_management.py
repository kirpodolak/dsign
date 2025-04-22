import os
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
        """Play playlist with optional profile"""
        from ..models import Playlist
        
        try:
            self._last_playback_state = {'status': 'playing', 'playlist_id': playlist_id}
            
            if not os.path.exists(PlaybackConstants.SOCKET_PATH):
                raise RuntimeError(f"MPV socket not found at {PlaybackConstants.SOCKET_PATH}")
            
            if not os.access(PlaybackConstants.SOCKET_PATH, os.R_OK | os.W_OK):
                raise PermissionError(f"No access to MPV socket at {PlaybackConstants.SOCKET_PATH}")
                
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            assigned_profile = self._mpv_manager.get_assigned_profile(playlist_id)
            if assigned_profile:
                self._mpv_manager.apply_profile(assigned_profile['id'])

            playlist_file = PlaybackUtils.create_playlist_file(self.upload_folder, self.tmp_dir, playlist)
            
            load_res = self._mpv_manager._send_command({
                "command": ["loadlist", str(playlist_file), "replace"]
            })
            
            loop_res = self._mpv_manager._send_command({
                "command": ["set_property", "loop-playlist", "inf"]
            })
            
            if not load_res or not loop_res:
                raise RuntimeError("Failed to set playlist properties")
            
            self._logo_manager._update_playback_status(playlist_id, 'playing')
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist': {'id': playlist.id, 'name': playlist.name},
                'settings': self._mpv_manager._current_settings
            })
            return True
            
        except Exception as e:
            self.logger.error(f"Playback error: {str(e)}", exc_info=True)
            self._logo_manager.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(self) -> bool:
        """Stop playback"""
        try:
            return self._logo_manager.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Stop error: {str(e)}", exc_info=True)
            return False

    def get_status(self) -> Dict:
        """Get current playback status"""
        from ..models import PlaybackStatus
        
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
            self.logger.error(f"MPV restart failed: {str(e)}", exc_info=True)
            return False

    def get_playback_info(self) -> Dict:
        """Get current playback info"""
        info = {}
        for category, settings in self._mpv_manager._current_settings.items():
            info[category] = {}
            for setting in settings.keys():
                response = self._mpv_manager._send_command({
                    "command": ["get_property", setting]
                })
                if response and 'data' in response:
                    info[category][setting] = response['data']
        return info
        
    def stop_idle_logo(self):
        """Stop idle logo display"""
        try:
            res = self._mpv_manager._send_command({"command": ["stop"]})
            if res is not None:
                self.logger.info("Idle logo stopped")
            else:
                self.logger.warning("Failed to get confirmation of idle logo stop")
        except Exception as e:
            self.logger.error(f"Failed to stop idle logo: {str(e)}")
            
    def restart_idle_logo(self) -> bool:
        """Restart idle logo display"""
        try:
            self.stop_idle_logo()
            return self._logo_manager.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Failed to restart idle logo: {str(e)}")
            return False
