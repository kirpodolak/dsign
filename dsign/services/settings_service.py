# settings_service.py
import os
import json
import logging
from typing import Optional, Dict, Any, Union
from pathlib import Path
from datetime import datetime
from flask import current_app
from dsign.extensions import db
from dsign.services.logger import ServiceLogger

class SettingsService:
    DEFAULT_SETTINGS = {
        "resolution": "1024x600",
        "aspect_ratio": "16:9",
        "rotation": 0,
        "overscan": False,
        "volume": 80,
        "mute": False,
        "display": {
            "theme": "light",
            "refresh_rate": 30,
            "logo": "default.png"
        }
    }

    def __init__(self, settings_file: str, upload_folder: str, logger: Optional[Union[logging.Logger, ServiceLogger]] = None):
        """
        Инициализация сервиса настроек
        
        Args:
            settings_file: Путь к файлу настроек
            upload_folder: Папка для загрузки файлов
            logger: Логгер (ServiceLogger или стандартный logging.Logger)
        """
        self.settings_file = Path(settings_file)
        self.upload_folder = Path(upload_folder)
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self._ensure_directories()

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'module': 'SettingsService'}
        if extra:
            extra_data.update(extra)
        self.logger.error(message, exc_info=exc_info, extra=extra_data)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'module': 'SettingsService'}
        if extra:
            extra_data.update(extra)
        self.logger.info(message, extra=extra_data)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'module': 'SettingsService'}
        if extra:
            extra_data.update(extra)
        self.logger.warning(message, extra=extra_data)

    def _ensure_directories(self) -> None:
        """Создание необходимых директорий"""
        try:
            self.upload_folder.mkdir(exist_ok=True, parents=True)
            if not self.settings_file.parent.exists():
                self.settings_file.parent.mkdir(parents=True)
        except Exception as e:
            self._log_error(f"Failed to create directories: {str(e)}", 
                          extra={'action': 'create_directories'})
            raise RuntimeError("Failed to create settings directories")

    def load_settings(self) -> Dict[str, Any]:
        """Загрузка настроек из файла"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, "r") as f:
                    settings = json.load(f)
                    self._log_info("Settings loaded from file", 
                                 extra={'file_path': str(self.settings_file)})
                    return {**self.DEFAULT_SETTINGS, **settings}
            self._log_info("Using default settings (file not found)", 
                         extra={'file_path': str(self.settings_file)})
            return self.DEFAULT_SETTINGS
        except json.JSONDecodeError as e:
            self._log_error(f"Invalid settings file format: {str(e)}", 
                          extra={'action': 'load_settings'})
            return self.DEFAULT_SETTINGS
        except Exception as e:
            self._log_error(f"Failed to load settings: {str(e)}", 
                          extra={'action': 'load_settings'})
            return self.DEFAULT_SETTINGS

    def save_settings(self, settings: Dict[str, Any]) -> None:
        """Сохранение настроек в файл"""
        try:
            with open(self.settings_file, "w") as f:
                json.dump({**self.DEFAULT_SETTINGS, **settings}, f, indent=4)
            self._log_info("Settings saved successfully", 
                         extra={'file_path': str(self.settings_file)})
        except Exception as e:
            self._log_error(f"Failed to save settings: {str(e)}", 
                          extra={'action': 'save_settings'})
            raise RuntimeError("Failed to save settings")

    def get_settings(self) -> Dict[str, Any]:
        """Получение всех настроек (алиас для load_settings)"""
        return self.load_settings()

    def get_display_settings(self) -> Dict[str, Any]:
        """Получение настроек отображения"""
        settings = self.load_settings()
        return settings.get('display', self.DEFAULT_SETTINGS['display'])

    def get_logo_path(self) -> str:
        """Получение пути к логотипу"""
        logo_path = self.upload_folder / 'idle_logo.jpg'
        if logo_path.exists():
            self._log_debug("Logo path retrieved", 
                          extra={'logo_path': str(logo_path)})
            return str(logo_path)
        self._log_warning("Logo file not found", 
                        extra={'logo_path': str(logo_path)})
        return ""

    def get_settings_for_active_profile(self) -> Dict[str, Any]:
        """
        Получение настроек активного профиля (idle или плейлиста)
        
        Returns:
            dict: Настройки активного профиля
        """
        try:
            from dsign.models import PlaybackStatus, PlaylistProfileAssignment, PlaybackProfile
            
            if not current_app:
                self._log_error("Application context not available", 
                              extra={'action': 'get_active_profile_settings'})
                return self.DEFAULT_SETTINGS

            with current_app.app_context():
                # Получаем текущий статус воспроизведения
                playback = db.session.query(PlaybackStatus).first()
                
                if playback and playback.status == 'playing' and playback.playlist_id:
                    # Если есть активный плейлист, получаем его профиль
                    assignment = db.session.query(PlaylistProfileAssignment).filter_by(
                        playlist_id=playback.playlist_id
                    ).first()
                    
                    if assignment:
                        profile = db.session.query(PlaybackProfile).get(assignment.profile_id)
                        if profile:
                            self._log_info("Using playlist profile settings", 
                                         extra={'playlist_id': playback.playlist_id, 
                                               'profile_id': assignment.profile_id})
                            return {**self.DEFAULT_SETTINGS, **profile.settings}
                
                # Если нет активного плейлиста, используем idle профиль
                idle_profile = db.session.query(PlaybackProfile).filter_by(
                    profile_type='idle'
                ).order_by(PlaybackProfile.id.desc()).first()
                
                if idle_profile:
                    self._log_info("Using idle profile settings", 
                                 extra={'profile_id': idle_profile.id})
                    return {**self.DEFAULT_SETTINGS, **idle_profile.settings}
                
                self._log_warning("No active profile found, using default settings")
                return self.DEFAULT_SETTINGS
            
        except Exception as e:
            self._log_error(f"Failed to get active profile settings: {str(e)}", 
                          extra={'action': 'get_active_profile_settings'})
            return self.DEFAULT_SETTINGS

    def get_current_settings(self) -> Dict[str, Any]:
        """Алиас для get_settings_for_active_profile (совместимость)"""
        return self.get_settings_for_active_profile()

    def update_mpv_settings(self, settings: Dict[str, Any], profile_type: str = None, playlist_id: int = None) -> bool:
        """
        Обновление настроек MPV и сохранение в профиль
        
        Args:
            settings: Словарь с настройками
            profile_type: Тип профиля ('idle' или 'playlist')
            playlist_id: ID плейлиста (если тип 'playlist')
            
        Returns:
            bool: Успешность операции
        """
        try:
            from .models import PlaylistProfileAssignment, PlaybackProfile
            
            if not current_app:
                self._log_error("Application context not available", 
                              extra={'action': 'update_mpv_settings'})
                return False

            with current_app.app_context():
                if playlist_id:
                    # Обновляем профиль плейлиста
                    assignment = db.session.query(PlaylistProfileAssignment).filter_by(
                        playlist_id=playlist_id
                    ).first()
                    
                    if assignment:
                        profile = db.session.query(PlaybackProfile).get(assignment.profile_id)
                        if profile:
                            profile.settings = settings
                            db.session.commit()
                            self._log_info("Updated playlist profile settings", 
                                         extra={'playlist_id': playlist_id, 
                                               'profile_id': assignment.profile_id})
                            return True
                elif profile_type:
                    # Обновляем idle профиль
                    profile = db.session.query(PlaybackProfile).filter_by(
                        profile_type=profile_type
                    ).first()
                    
                    if profile:
                        profile.settings = settings
                        db.session.commit()
                        self._log_info("Updated idle profile settings", 
                                     extra={'profile_id': profile.id})
                        return True
                
                self._log_warning("No matching profile found for update", 
                                extra={'profile_type': profile_type, 
                                      'playlist_id': playlist_id})
                return False
                
        except Exception as e:
            self._log_error(f"Failed to update MPV settings: {str(e)}", 
                          extra={'action': 'update_mpv_settings'})
            if current_app:
                db.session.rollback()
            return False
