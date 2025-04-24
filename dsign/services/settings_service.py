# settings_service.py
import os
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime
from flask import current_app
from dsign.extensions import db  # Используем единый экземпляр db из extensions

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

    def __init__(self, settings_file: str, upload_folder: str, logger: Optional[logging.Logger] = None):
        """
        Инициализация сервиса настроек
        
        Args:
            settings_file: Путь к файлу настроек
            upload_folder: Папка для загрузки файлов
            logger: Логгер (опционально)
        """
        self.settings_file = Path(settings_file)
        self.upload_folder = Path(upload_folder)
        self.logger = logger or logging.getLogger(__name__)
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Создание необходимых директорий"""
        try:
            self.upload_folder.mkdir(exist_ok=True, parents=True)
            if not self.settings_file.parent.exists():
                self.settings_file.parent.mkdir(parents=True)
        except Exception as e:
            self.logger.error(f"Failed to create directories: {str(e)}", exc_info=True)
            raise RuntimeError("Failed to create settings directories")

    def load_settings(self) -> Dict[str, Any]:
        """Загрузка настроек из файла"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, "r") as f:
                    settings = json.load(f)
                    return {**self.DEFAULT_SETTINGS, **settings}
            return self.DEFAULT_SETTINGS
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid settings file format: {str(e)}")
            return self.DEFAULT_SETTINGS
        except Exception as e:
            self.logger.error(f"Failed to load settings: {str(e)}", exc_info=True)
            return self.DEFAULT_SETTINGS

    def save_settings(self, settings: Dict[str, Any]) -> None:
        """Сохранение настроек в файл"""
        try:
            with open(self.settings_file, "w") as f:
                json.dump({**self.DEFAULT_SETTINGS, **settings}, f, indent=4)
            self.logger.info("Settings saved successfully")
        except Exception as e:
            self.logger.error(f"Failed to save settings: {str(e)}", exc_info=True)
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
        return str(logo_path) if logo_path.exists() else ""

    def get_settings_for_active_profile(self) -> Dict[str, Any]:
        """
        Получение настроек активного профиля (idle или плейлиста)
        
        Returns:
            dict: Настройки активного профиля
        """
        try:
            from dsign.models import PlaybackStatus, PlaylistProfileAssignment, PlaybackProfile
            
            if not current_app:
                self.logger.error("Application context not available")
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
                            return {**self.DEFAULT_SETTINGS, **profile.settings}
                
                # Если нет активного плейлиста, используем idle профиль
                idle_profile = db.session.query(PlaybackProfile).filter_by(
                    profile_type='idle'
                ).order_by(PlaybackProfile.id.desc()).first()
                
                return {**self.DEFAULT_SETTINGS, **idle_profile.settings} if idle_profile else self.DEFAULT_SETTINGS
            
        except Exception as e:
            self.logger.error(f"Failed to get active profile settings: {str(e)}", exc_info=True)
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
                self.logger.error("Application context not available")
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
                            return True
                elif profile_type:
                    # Обновляем idle профиль
                    profile = db.session.query(PlaybackProfile).filter_by(
                        profile_type=profile_type
                    ).first()
                    
                    if profile:
                        profile.settings = settings
                        db.session.commit()
                        return True
                
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to update MPV settings: {str(e)}", exc_info=True)
            if current_app:
                db.session.rollback()
            return False
