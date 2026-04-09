import os
import json
import subprocess
import traceback
from typing import Dict, Optional
from pathlib import Path

from .playback_constants import PlaybackConstants
from .playback_utils import PlaybackUtils

class PlaylistManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager, logo_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        # `db_session` is inconsistently passed around the codebase:
        # sometimes it's a Flask-SQLAlchemy `db` object, sometimes it's `db.session`.
        # Normalize to a real Session that has `.query()/.add()/.commit()/.rollback()`.
        self.db_session = getattr(db_session, 'session', db_session)
        self._mpv_manager = mpv_manager
        self._logo_manager = logo_manager
        self._last_playback_state = {}
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)

    def play(self, playlist_id: int) -> bool:
        """Play playlist with profile support"""
        from ..models import PlaybackStatus, Playlist, PlaylistProfileAssignment, PlaybackProfile
    
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
                    # `settings` is stored as JSON in DB, so it is already a dict.
                    profile_settings = profile.settings or {}

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
        
            # Load ffconcat "playlist" as a file (mpv auto-detects ffconcat by header)
            result = self._mpv_manager._send_command({
                "command": ["loadfile", str(playlist_file), "replace"]
            }, timeout=10.0)
        
            if not result or result.get("error") != "success":
                raise RuntimeError("Failed to load playlist")

            # Update playback status in DB only after MPV accepted the playlist
            playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
            playback.playlist_id = playlist_id
            playback.status = 'playing'
            self.db_session.add(playback)
            self.db_session.commit()

            # Ensure looping is enabled
            self._mpv_manager._send_command({
                "command": ["set_property", "loop-playlist", "inf"]
            })

            # Start playback
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"]
            })

            # Notify clients
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist': {'id': playlist.id, 'name': playlist.name},
                'settings': profile_settings
            })
        
            return True

        except Exception as e:
            self.logger.error(
                "Playback error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
            try:
                self.db_session.rollback()
            except Exception:
                pass

            # Best-effort: persist non-playing state so UI doesn't show green when we fell back to idle
            try:
                from ..models import PlaybackStatus
                playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
                playback.status = 'idle'
                playback.playlist_id = None
                self.db_session.add(playback)
                self.db_session.commit()
            except Exception:
                try:
                    self.db_session.rollback()
                except Exception:
                    pass

            # Fall back to idle logo
            self._logo_manager.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(self) -> bool:
        """Stop playback"""
        try:
            return self._logo_manager.display_idle_logo()
        except Exception as e:
            self.logger.error(
                "Stop error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
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
            self.logger.error(
                "MPV restart failed",
                extra={
                    "error": str(e),
                    "type": type(e).__name__,
                    "stack_trace": traceback.format_exc()
                }
            )
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
