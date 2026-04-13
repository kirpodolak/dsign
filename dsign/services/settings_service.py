# settings_service.py
import os
import json
import logging
import traceback
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
            "logo": "default.png",
            # HDMI output mode preset. "auto" relies on EDID; other values are applied via config.txt + reboot.
            # Valid values: "auto" | "1080p60" | "4k30"
            "hdmi_mode_preset": "auto",
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

        # Lightweight in-process cache to avoid repeated DB hits on frequent polling.
        # (Each process has its own cache; safe for correctness with short TTL.)
        self._cached_current_settings: Optional[Dict[str, Any]] = None
        self._cached_current_settings_ts: float = 0.0
        self._current_settings_cache_ttl_sec: float = 1.0

    def _log_error(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'module': 'SettingsService'}
        if extra:
            extra_data.update(extra)
        # ServiceLogger.error does not accept exc_info; include stack trace as structured data.
        extra_data.setdefault('stack_trace', traceback.format_exc())
        self.logger.error(message, extra=extra_data)

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
                self._log_error(
                    "Application context not available",
                    extra={'action': 'get_active_profile_settings'}
                )
                return self.DEFAULT_SETTINGS

            now_ts = datetime.now().timestamp()
            if (
                self._cached_current_settings is not None
                and (now_ts - self._cached_current_settings_ts) < self._current_settings_cache_ttl_sec
            ):
                return self._cached_current_settings

            # Base settings are stored in a JSON file (global settings like UI/display).
            # Profile settings are stored in DB and should override MPV-related keys when active.
            base_settings = self.load_settings()

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
                        self._log_info(
                            "Using playlist profile settings",
                            extra={'playlist_id': playback.playlist_id, 'profile_id': assignment.profile_id}
                        )
                        settings = {**self.DEFAULT_SETTINGS, **base_settings, **(profile.settings or {})}
                        self._cached_current_settings = settings
                        self._cached_current_settings_ts = now_ts
                        return settings
            
            # Если нет активного плейлиста, используем idle профиль
            idle_profile = db.session.query(PlaybackProfile).filter_by(
                profile_type='idle'
            ).order_by(PlaybackProfile.id.desc()).first()
            
            if idle_profile:
                self._log_info("Using idle profile settings", extra={'profile_id': idle_profile.id})
                settings = {**self.DEFAULT_SETTINGS, **base_settings, **(idle_profile.settings or {})}
                self._cached_current_settings = settings
                self._cached_current_settings_ts = now_ts
                return settings

                # If there is no idle profile at all, auto-seed one to avoid repeated warnings
                # and to provide a stable "default" profile that can later be edited via UI/API.
                try:
                    seeded = PlaybackProfile(
                        name="Default idle",
                        profile_type='idle',
                        settings=dict(self.DEFAULT_SETTINGS),
                    )
                    db.session.add(seeded)
                    db.session.commit()
                    self._log_info("Seeded default idle profile", extra={'profile_id': seeded.id})
                    settings = {**self.DEFAULT_SETTINGS, **base_settings, **(seeded.settings or {})}
                    self._cached_current_settings = settings
                    self._cached_current_settings_ts = now_ts
                    return settings
                except Exception as e:
                    db.session.rollback()
                    self._log_warning("No active profile found, using default settings", extra={'seed_error': str(e)})
                    return {**self.DEFAULT_SETTINGS, **base_settings}
            return {**self.DEFAULT_SETTINGS, **base_settings}
            
        except Exception as e:
            self._log_error(
                f"Failed to get active profile settings: {str(e)}",
                extra={'action': 'get_active_profile_settings'}
            )
            return self.DEFAULT_SETTINGS

    def get_current_settings(self) -> Dict[str, Any]:
        """Алиас для get_settings_for_active_profile (совместимость)"""
        return self.get_settings_for_active_profile()

    def set_display_mode_preset(self, preset: str) -> Dict[str, Any]:
        """
        Persist HDMI mode preset in settings.json.

        preset: "auto" | "1080p60" | "4k30"
        """
        if preset not in {"auto", "1080p60", "4k30"}:
            raise ValueError("Invalid display preset")

        settings = self.load_settings()
        display = settings.get("display") if isinstance(settings.get("display"), dict) else {}
        display["hdmi_mode_preset"] = preset
        settings["display"] = display
        self.save_settings(settings)

        # Invalidate cache so UI immediately reflects the updated global setting.
        self._cached_current_settings = None
        self._cached_current_settings_ts = 0.0
        return settings

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
            from dsign.models import PlaylistProfileAssignment, PlaybackProfile
            
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
