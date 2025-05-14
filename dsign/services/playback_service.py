import os
import time
import socket
import subprocess
from pathlib import Path
from typing import Dict, Optional, List, Union, Any
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
import logging

from .mpv_management import MPVManager
from .logo_management import LogoManager
from .profile_management import ProfileManager
from .playlist_management import PlaylistManager
from .playback_constants import PlaybackConstants
from .logger import ServiceLogger

class PlaybackService:
    def __init__(self, upload_folder: str, db_session, socketio, logger: Optional[Union[logging.Logger, ServiceLogger]] = None):
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self.socketio = socketio
        self.mpv_socket = PlaybackConstants.SOCKET_PATH
        
        # Initialize components with consistent logging
        self._mpv_manager = MPVManager(
            logger=self.logger,
            socketio=self.socketio,
            mpv_socket=PlaybackConstants.SOCKET_PATH,
            upload_folder=str(self.upload_folder))
            
        self._logo_manager = LogoManager(
            logger=self.logger,
            socketio=self.socketio,
            upload_folder=str(self.upload_folder),
            db_session=self.db_session,
            mpv_manager=self._mpv_manager
        )
            
        self._profile_manager = ProfileManager(
            self.logger, 
            self.db_session, 
            self._mpv_manager
        )
        
        self._playlist_manager = PlaylistManager(
            self.logger, 
            self.socketio, 
            self.upload_folder,
            self.db_session, 
            self._mpv_manager, 
            self._logo_manager
        )
        
        # Initialize with retry
        self._init_with_retry()

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        self.logger.error(message, exc_info=exc_info, extra=extra_data)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        self.logger.info(message, extra=extra_data)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        self.logger.warning(message, extra=extra_data)

    def _check_mpv_service_active(self) -> bool:
        """Check if dsign-mpv.service is active using systemctl"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "dsign-mpv.service"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                self._log_warning(f"MPV service not active: {result.stderr.strip()}", 
                                 extra={'action': 'service_check'})
                return False
            return result.stdout.strip() == "active"
        except subprocess.TimeoutExpired:
            self._log_error("Timeout while checking MPV service status", 
                          extra={'action': 'service_check'})
            return False
        except Exception as e:
            self._log_error(f"Failed to check MPV service status: {str(e)}", 
                          extra={'action': 'service_check'})
            return False

    def _check_ipc_connection(self) -> bool:
        """Check if we can connect to MPV IPC socket"""
        try:
            # 1. Проверяем, что сервис MPV активен
            result = subprocess.run(
                ["systemctl", "is-active", "dsign-mpv.service"],
                capture_output=True,
                text=True
            )
            if result.stdout.strip() != "active":
                self._log_error("MPV service is not active", 
                              extra={'action': 'ipc_check'})
                return False

            # 2. Проверяем существование сокета
            if not os.path.exists(self.mpv_socket):
                self._log_error(f"MPV socket not found at {self.mpv_socket}", 
                              extra={'action': 'ipc_check'})
                return False

            # 3. Проверяем соединение через простую команду
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(5)
                    s.connect(self.mpv_socket)
                    s.sendall(b'{ "command": ["get_property", "version"] }\n')
                    response = s.recv(1024)
                    if response:
                        return True
            except Exception as e:
                self._log_error(f"Socket communication error: {str(e)}", 
                              extra={'action': 'ipc_check'})
                return False

            return False
        
        except Exception as e:
            self._log_error(f"IPC connection check failed: {str(e)}", 
                          extra={'action': 'ipc_check'})
            return False

    def _init_with_retry(self, max_attempts: int = 3, initial_delay: float = 2.0):
        """Optimized initialization with parallel checks and backoff"""
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                delay = min(initial_delay * (2 ** attempt), 30)
                
                with ThreadPoolExecutor(max_workers=3) as executor:
                    checks = {
                        'mpv': executor.submit(self._check_mpv_service_active),
                        'ipc': executor.submit(self._check_ipc_connection),
                        'init': executor.submit(self._mpv_manager.initialize)
                    }
                    
                    for name, future in checks.items():
                        if not future.result(timeout=10):
                            raise RuntimeError(f"{name} check failed")
                    
                    Thread(target=self._preload_resources).start()
                    return
                    
            except Exception as e:
                last_exception = e
                self._log_error(f"Attempt {attempt+1} failed: {str(e)}", 
                              extra={'attempt': attempt+1, 'action': 'init'})
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        self._log_error("Initialization failed after all attempts", 
                      extra={'action': 'init', 'status': 'failed'})
        raise RuntimeError(f"Initialization failed: {str(last_exception)}")

    def _preload_resources(self):
        """Non-critical resource loading in background"""
        try:
            self._logo_manager._initialize_default_logo()
            logo_path = self._logo_manager.get_current_logo_path()
            if not logo_path.exists():
                self._log_warning(f"Logo file missing: {logo_path}", 
                                extra={'action': 'preload_resources'})
            
            self._transition_to_idle()
        except Exception as e:
            self._log_error(f"Background init error: {str(e)}", 
                          extra={'action': 'preload_resources'})

    def _transition_to_idle(self):
        """Transition to idle state with logo"""
        max_attempts = 5
        delay = 2
    
        for attempt in range(max_attempts):
            try:
                if self._logo_manager.display_idle_logo():
                    self._log_info("Successfully transitioned to idle state", 
                                 extra={'action': 'transition_to_idle'})
                    return
                self._log_warning(f"Idle logo attempt {attempt+1}/{max_attempts} failed", 
                                extra={'attempt': attempt+1, 'action': 'transition_to_idle'})
            except Exception as e:
                self._log_error(f"Idle transition error: {str(e)}", 
                              extra={'attempt': attempt+1, 'action': 'transition_to_idle'})
        
            time.sleep(delay)
    
        self._log_error("Could not establish idle state", 
                      extra={'action': 'transition_to_idle', 'status': 'failed'})
        raise RuntimeError("Could not establish idle state")

    # Делегированные методы
    def play(self, playlist_id: int) -> bool:
        """Play specified playlist"""
        try:
            result = self._playlist_manager.play(playlist_id)
            self._log_info(f"Playing playlist {playlist_id}", 
                         extra={'playlist_id': playlist_id, 'action': 'play'})
            return result
        except Exception as e:
            self._log_error(f"Error playing playlist {playlist_id}: {str(e)}", 
                          extra={'playlist_id': playlist_id, 'action': 'play'})
            return False

    def stop(self) -> bool:
        """Stop playback and return to idle state"""
        try:
            result = self._playlist_manager.stop()
            self._log_info("Playback stopped", extra={'action': 'stop'})
            return result
        except Exception as e:
            self._log_error(f"Error stopping playback: {str(e)}", 
                          extra={'action': 'stop'})
            return False

    def get_status(self) -> Dict:
        """Get current playback status"""
        try:
            return self._playlist_manager.get_status()
        except Exception as e:
            self._log_error(f"Error getting status: {str(e)}", 
                          extra={'action': 'get_status'})
            return {'error': str(e)}

    def restart_mpv(self) -> bool:
        """Restart MPV process"""
        try:
            result = self._playlist_manager.restart_mpv()
            self._log_info("MPV restarted", extra={'action': 'restart_mpv'})
            return result
        except Exception as e:
            self._log_error(f"Error restarting MPV: {str(e)}", 
                          extra={'action': 'restart_mpv'})
            return False

    def get_playback_info(self) -> Dict:
        """Get detailed playback information"""
        try:
            return self._playlist_manager.get_playback_info()
        except Exception as e:
            self._log_error(f"Error getting playback info: {str(e)}", 
                          extra={'action': 'get_playback_info'})
            return {'error': str(e)}

    def stop_idle_logo(self):
        """Stop displaying idle logo"""
        try:
            result = self._playlist_manager.stop_idle_logo()
            self._log_info("Idle logo stopped", extra={'action': 'stop_idle_logo'})
            return result
        except Exception as e:
            self._log_error(f"Error stopping idle logo: {str(e)}", 
                          extra={'action': 'stop_idle_logo'})
            return False

    def restart_idle_logo(self) -> bool:
        """Restart idle logo display"""
        try:
            result = self._playlist_manager.restart_idle_logo()
            self._log_info("Idle logo restarted", extra={'action': 'restart_idle_logo'})
            return result
        except Exception as e:
            self._log_error(f"Error restarting idle logo: {str(e)}", 
                          extra={'action': 'restart_idle_logo'})
            return False

    def display_idle_logo(self) -> bool:
        """Display idle logo without profile_id parameter"""
        try:
            result = self._logo_manager.display_idle_logo()
            self._log_info("Displaying idle logo", extra={'action': 'display_idle_logo'})
            return result
        except Exception as e:
            self._log_error(f"Error displaying idle logo: {str(e)}", 
                          extra={'action': 'display_idle_logo'})
            return False

    def get_current_logo_path(self) -> Path:
        """Get path to current logo file"""
        try:
            return self._logo_manager.get_current_logo_path()
        except Exception as e:
            self._log_error(f"Error getting logo path: {str(e)}", 
                          extra={'action': 'get_current_logo_path'})
            return Path()

    def get_current_logo_status(self) -> dict:
        """Get status information about current logo"""
        try:
            return self._logo_manager.get_current_logo_status()
        except Exception as e:
            self._log_error(f"Error getting logo status: {str(e)}", 
                          extra={'action': 'get_current_logo_status'})
            return {'error': str(e)}

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get playback profile by ID"""
        try:
            return self._profile_manager.get_profile(profile_id)
        except Exception as e:
            self._log_error(f"Error getting profile {profile_id}: {str(e)}", 
                          extra={'profile_id': profile_id, 'action': 'get_profile'})
            return None

    def get_all_profiles(self, profile_type: str = None) -> List[Dict]:
        """Get all playback profiles"""
        try:
            return self._profile_manager.get_all_profiles(profile_type)
        except Exception as e:
            self._log_error(f"Error getting profiles: {str(e)}", 
                          extra={'profile_type': profile_type, 'action': 'get_all_profiles'})
            return []

    def create_profile(self, name: str, profile_type: str, settings: Dict) -> Optional[int]:
        """Create new playback profile"""
        try:
            profile_id = self._profile_manager.create_profile(name, profile_type, settings)
            self._log_info(f"Created profile {profile_id}", 
                         extra={'profile_id': profile_id, 'action': 'create_profile'})
            return profile_id
        except Exception as e:
            self._log_error(f"Error creating profile: {str(e)}", 
                          extra={'action': 'create_profile'})
            return None

    def update_profile(self, profile_id: int, name: str, settings: Dict) -> bool:
        """Update existing playback profile"""
        try:
            result = self._profile_manager.update_profile(profile_id, name, settings)
            self._log_info(f"Updated profile {profile_id}", 
                         extra={'profile_id': profile_id, 'action': 'update_profile'})
            return result
        except Exception as e:
            self._log_error(f"Error updating profile {profile_id}: {str(e)}", 
                          extra={'profile_id': profile_id, 'action': 'update_profile'})
            return False

    def delete_profile(self, profile_id: int) -> bool:
        """Delete playback profile"""
        try:
            result = self._profile_manager.delete_profile(profile_id)
            self._log_info(f"Deleted profile {profile_id}", 
                         extra={'profile_id': profile_id, 'action': 'delete_profile'})
            return result
        except Exception as e:
            self._log_error(f"Error deleting profile {profile_id}: {str(e)}", 
                          extra={'profile_id': profile_id, 'action': 'delete_profile'})
            return False

    def get_assigned_profile(self, playlist_id: int) -> Optional[Dict]:
        """Get profile assigned to playlist"""
        try:
            return self._profile_manager.get_assigned_profile(playlist_id)
        except Exception as e:
            self._log_error(f"Error getting assigned profile for playlist {playlist_id}: {str(e)}", 
                          extra={'playlist_id': playlist_id, 'action': 'get_assigned_profile'})
            return None

    def assign_profile_to_playlist(self, playlist_id: int, profile_id: int) -> bool:
        """Assign profile to playlist"""
        try:
            result = self._profile_manager.assign_profile_to_playlist(playlist_id, profile_id)
            self._log_info(f"Assigned profile {profile_id} to playlist {playlist_id}", 
                         extra={'playlist_id': playlist_id, 'profile_id': profile_id, 'action': 'assign_profile'})
            return result
        except Exception as e:
            self._log_error(f"Error assigning profile to playlist: {str(e)}", 
                          extra={'playlist_id': playlist_id, 'profile_id': profile_id, 'action': 'assign_profile'})
            return False

    def apply_profile(self, profile_id: int) -> bool:
        """Apply profile settings"""
        try:
            result = self._profile_manager.apply_profile(profile_id)
            self._log_info(f"Applied profile {profile_id}", 
                         extra={'profile_id': profile_id, 'action': 'apply_profile'})
            return result
        except Exception as e:
            self._log_error(f"Error applying profile {profile_id}: {str(e)}", 
                          extra={'profile_id': profile_id, 'action': 'apply_profile'})
            return False

    def verify_settings_support(self) -> Dict:
        """Verify supported settings"""
        try:
            return self._mpv_manager.verify_settings_support()
        except Exception as e:
            self._log_error(f"Error verifying settings support: {str(e)}", 
                          extra={'action': 'verify_settings_support'})
            return {'error': str(e)}

    def update_settings(self, settings: Dict) -> bool:
        """Update playback settings"""
        try:
            result = self._mpv_manager.update_settings(settings)
            self._log_info("Updated playback settings", 
                         extra={'action': 'update_settings'})
            return result
        except Exception as e:
            self._log_error(f"Error updating settings: {str(e)}", 
                          extra={'action': 'update_settings'})
            return False
            
    def _verify_mpv_state(self) -> bool:
        """Проверяем, что MPV действительно воспроизводит контент"""
        try:
            status = self._mpv_manager._send_command({
                "command": ["get_property", "idle-active"]
            })
            return status and not status.get("data", True)
        except Exception as e:
            self._log_error(f"MPV state verification failed: {str(e)}", 
                          extra={'action': 'verify_mpv_state'})
            return False
            
    def wait_for_mpv_ready(self, timeout=30, check_interval=1):
        """Явное ожидание готовности MPV"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._check_mpv_service_active() and self._check_ipc_connection():
                self._log_info("MPV is ready", extra={'action': 'wait_for_mpv_ready'})
                return True
            time.sleep(check_interval)
        self._log_error("Timeout waiting for MPV to be ready", 
                      extra={'timeout': timeout, 'action': 'wait_for_mpv_ready'})
        return False
        
    def play_file(self, file_info):
        """Воспроизводит файл с учетом его типа"""
        try:
            if file_info.get('is_video'):
                self._play_video_full(file_info['filename'])
                self._log_info(f"Playing video file: {file_info['filename']}", 
                             extra={'filename': file_info['filename'], 'action': 'play_file'})
            else:
                self._show_image(
                    file_info['filename'],
                    duration=file_info['duration']
                )
                self._log_info(f"Showing image: {file_info['filename']}", 
                             extra={'filename': file_info['filename'], 'duration': file_info['duration'], 'action': 'play_file'})
        except Exception as e:
            self._log_error(f"Error playing file: {str(e)}", 
                          extra={'filename': file_info.get('filename'), 'action': 'play_file'})
            raise
