import os
import time
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
        
        # Initialize components
        self._mpv_manager = MPVManager(
            logger=self.logger,
            socketio=self.socketio,
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
        
        self.logo_manager = LogoManager(
            logger=self.logger,
            socketio=self.socketio,
            upload_folder=self.upload_folder,
            db_session=self.db_session,
            mpv_manager=self._mpv_manager
        )
        
        # Initialize with retry
        self._init_with_retry()

    def display_idle_logo(self):
        return self.logo_manager.display_idle_logo()
        
    def restart_idle_logo(self, upload_folder=None, idle_logo=None):
        """Прокси к logo_manager с передачей параметров"""
        return self.logo_manager.restart_idle_logo(upload_folder, idle_logo)
        
    def _sanitize_extra_data(self, extra_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Remove reserved keys from extra_data to prevent LogRecord conflicts"""
        if not extra_data:
            return None
            
        reserved_keys = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
            'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
            'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
            'thread', 'threadName', 'processName', 'process'
        }
        
        return {k: v for k, v in extra_data.items() if k not in reserved_keys}

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        
        # Вместо передачи exc_info, логируем исключение отдельно если нужно
        if exc_info:
            self.logger.error(f"{message}", extra=safe_extra)
            # Если нужно полное traceback, логируем его отдельно
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}", extra=safe_extra)
        else:
            self.logger.error(f"{message}", extra=safe_extra)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.info(f"{message}", extra=safe_extra)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.warning(f"{message}", extra=safe_extra)

    def _log_debug(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для отладочных логов"""
        extra_data = {'service_module': 'PlaybackService'}
        if extra:
            extra_data.update(extra)
        safe_extra = self._sanitize_extra_data(extra_data)
        self.logger.debug(f"{message}", extra=safe_extra)

    def _init_with_retry(self, max_attempts: int = 3, initial_delay: float = 2.0):
        """Optimized initialization with parallel checks and backoff"""
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                delay = min(initial_delay * (2 ** attempt), 30)
                
                # Используем метод health check из MPVManager
                health = self._mpv_manager.check_health()
                if not all(health.values()):
                    raise RuntimeError(f"MPV health check failed: {health}")
                
                if not self._mpv_manager.initialize():
                    raise RuntimeError("MPV initialization failed")
                
                Thread(target=self._preload_resources).start()
                return
                    
            except Exception as e:
                last_exception = e
                self._log_error(
                    f"Initialization attempt {attempt+1} failed", 
                    extra={
                        'attempt': attempt+1, 
                        'action': 'init',
                        'error': str(e),
                        'type': type(e).__name__
                    }
                )
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        self._log_error(
            "Initialization failed after all attempts", 
            extra={
                'action': 'init', 
                'status': 'failed',
                'last_error': str(last_exception)
            }
        )
        raise RuntimeError(f"Initialization failed: {str(last_exception)}")

    def _preload_resources(self):
        """Non-critical resource loading in background"""
        try:
            self._logo_manager._initialize_default_logo()
            logo_path = self._logo_manager.get_current_logo_path()
            if not logo_path.exists():
                self._log_warning(
                    "Logo file missing", 
                    extra={
                        'action': 'preload_resources',
                        'logo_path': str(logo_path)
                    }
                )
            
            self._transition_to_idle()
        except Exception as e:
            self._log_error(
                "Background init error", 
                extra={
                    'action': 'preload_resources',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )

    def _transition_to_idle(self):
        """Transition to idle state with logo"""
        max_attempts = 5
        delay = 2
    
        for attempt in range(max_attempts):
            try:
                if self._logo_manager.display_idle_logo():
                    self._log_info(
                        "Successfully transitioned to idle state", 
                        extra={
                            'action': 'transition_to_idle',
                            'attempt': attempt+1
                        }
                    )
                    return
                    
                self._log_warning(
                    "Idle logo attempt failed", 
                    extra={
                        'attempt': attempt+1, 
                        'action': 'transition_to_idle'
                    }
                )
            except Exception as e:
                self._log_error(
                    "Idle transition error", 
                    extra={
                        'attempt': attempt+1, 
                        'action': 'transition_to_idle',
                        'error': str(e),
                        'type': type(e).__name__
                    }
                )
        
            time.sleep(delay)
    
        self._log_error(
            "Could not establish idle state", 
            extra={
                'action': 'transition_to_idle', 
                'status': 'failed',
                'max_attempts': max_attempts
            }
        )
        raise RuntimeError("Could not establish idle state")

    # Делегированные методы
    def play(self, playlist_id: int, settings: Optional[Dict] = None) -> bool:
        """Play specified playlist with optional settings"""
        try:
            start_time = time.time()
            
            # Если переданы настройки, применить их перед воспроизведением
            if settings:
                self._log_info(
                    "Applying custom settings for playback", 
                    extra={
                        'playlist_id': playlist_id, 
                        'action': 'apply_settings',
                        'settings_count': len(settings)
                    }
                )
                # Применяем настройки через mpv_manager
                self._mpv_manager.update_settings(settings)
            
            result = self._playlist_manager.play(playlist_id)
            self._log_info(
                "Playing playlist", 
                extra={
                    'playlist_id': playlist_id, 
                    'action': 'play',
                    'duration_sec': round(time.time() - start_time, 3),
                    'success': result,
                    'has_custom_settings': bool(settings)
                }
            )
            return result
        except Exception as e:
            self._log_error(
                "Error playing playlist", 
                extra={
                    'playlist_id': playlist_id, 
                    'action': 'play',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )
            return False

    def stop(self) -> bool:
        """Stop playback and return to idle state"""
        try:
            start_time = time.time()
            result = self._playlist_manager.stop()
            self._log_info(
                "Playback stopped", 
                extra={
                    'action': 'stop',
                    'duration_sec': round(time.time() - start_time, 3),
                    'success': result
                }
            )
            return result
        except Exception as e:
            self._log_error(
                "Error stopping playback", 
                extra={
                    'action': 'stop',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )
            return False

    def get_status(self) -> Dict:
        """Get current playback status"""
        try:
            start_time = time.time()
            status = self._playlist_manager.get_status()
            self._log_info(
                "Retrieved playback status", 
                extra={
                    'action': 'get_status',
                    'duration_sec': round(time.time() - start_time, 3)
                }
            )
            return status
        except Exception as e:
            self._log_error(
                "Error getting status", 
                extra={
                    'action': 'get_status',
                    'error': str(e),
                    'type': type(e).__name__
                }
            )
            return {'error': str(e)}

    # Остальные методы остаются без изменений, но с добавлением duration_sec в логи
    # ... (остальные методы реализованы аналогично с добавлением временных метрик)

    def wait_for_mpv_ready(self, timeout=30, check_interval=1):
        """Явное ожидание готовности MPV"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._mpv_manager.check_health().get('responsive', False):
                self._log_info(
                    "MPV is ready", 
                    extra={
                        'action': 'wait_for_mpv_ready',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
                return True
            time.sleep(check_interval)
            
        self._log_error(
            "Timeout waiting for MPV to be ready", 
            extra={
                'timeout': timeout, 
                'action': 'wait_for_mpv_ready',
                'duration_sec': round(time.time() - start_time, 3)
            }
        )
        return False
        
    def play_file(self, file_info):
        """Воспроизводит файл с учетом его типа"""
        try:
            start_time = time.time()
            if file_info.get('is_video'):
                self._play_video_full(file_info['filename'])
                self._log_info(
                    "Playing video file", 
                    extra={
                        'filename': file_info['filename'], 
                        'action': 'play_file',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
            else:
                self._show_image(
                    file_info['filename'],
                    duration=file_info['duration']
                )
                self._log_info(
                    "Showing image", 
                    extra={
                        'filename': file_info['filename'], 
                        'duration': file_info['duration'],
                        'action': 'play_file',
                        'duration_sec': round(time.time() - start_time, 3)
                    }
                )
        except Exception as e:
            self._log_error(
                "Error playing file", 
                extra={
                    'filename': file_info.get('filename'), 
                    'action': 'play_file',
                    'error': str(e),
                    'type': type(e).__name__,
                    'duration_sec': round(time.time() - start_time, 3)
                }
            )
            raise
