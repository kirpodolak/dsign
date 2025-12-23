import os
import json
import time
import subprocess
import re
from typing import Dict, Optional, List, Any
from pathlib import Path
from datetime import datetime

from .playback_constants import PlaybackConstants
from .playback_utils import PlaybackUtils
from .m3u_manager import M3UManager
from .playlist_service import PlaylistService


class PlaylistManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager, logo_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        self.db_session = db_session
        self._mpv_manager = mpv_manager
        self._logo_manager = logo_manager
        self._last_playback_state = {}
        
        # Пути из конфигурации
        try:
            from flask import current_app
            self.config = current_app.config
        except:
            from .. import config
            self.config = config.config
        
        # Пути к файлам и скриптам
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        # Путь к Lua скрипту слайд-шоу (используем путь из конфига)
        self.scripts_dir = Path(self.config.get('SCRIPTS_DIR', '/home/dsign/dsign/static/scripts'))
        self.lua_script_path = self.scripts_dir / 'slideshow.lua'
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        
        # Сохраняем Lua скрипт если его нет
        self._ensure_lua_script()
        
        # Пути к конфигурации из конфига
        self.m3u_export_dir = Path(self.config.get('M3U_EXPORT_DIR', '/home/dsign/dsign/static/playlists'))
        self.media_root = Path(self.config.get('MEDIA_ROOT', '/var/lib/dsign/media'))
        self.use_slideshow = self.config.get('USE_SLIDESHOW', True)
        
        # Создаем папки если не существуют
        self.m3u_export_dir.mkdir(parents=True, exist_ok=True)
        
        # Инициализируем менеджеры
        self.m3u_manager = M3UManager(
            logger=logger,
            media_root=self.media_root,
            upload_folder=self.upload_folder
        )
        
        # Инициализируем сервис плейлистов
        self.playlist_service = PlaylistService(db_session, logger)
        
        self.logger.info(f"Папка с M3U плейлистами: {self.m3u_export_dir.absolute()}")
        self.logger.info(f"Папка с медиафайлами: {self.media_root.absolute()}")
        self.logger.info(f"Lua скрипт слайд-шоу: {self.lua_script_path.absolute()}")
        self.logger.info(f"Использовать слайд-шоу: {self.use_slideshow}")
        
    def _ensure_lua_script(self):
        """Проверяет и обновляет Lua скрипт при необходимости"""
        source_script = Path('/dsign/static/scripts/slideshow.lua')
        target_script = self.lua_script_path
    
        # Если целевого скрипта нет, создаем его
        if not target_script.exists():
            return self._copy_or_link_script(source_script, target_script)
    
        # Если скрипт есть, проверяем его актуальность
        try:
            # Сравниваем даты модификации
            source_mtime = source_script.stat().st_mtime if source_script.exists() else 0
            target_mtime = target_script.stat().st_mtime
        
            # Если исходный скрипт новее, обновляем
            if source_mtime > target_mtime + 1:  # +1 секунда для погрешности
                self.logger.info(f"Обнаружена новая версия Lua скрипта")
                return self._copy_or_link_script(source_script, target_script, force=True)
            else:
                self.logger.debug(f"Lua скрипт актуален: {target_script}")
                return True
            
        except Exception as e:
            self.logger.warning(f"Ошибка проверки скрипта: {e}")
            return True  # Продолжаем с существующим скриптом

    def _copy_or_link_script(self, source_script: Path, target_script: Path, force: bool = False) -> bool:
        """Копирует или создает ссылку на Lua скрипт"""
        try:
            if not source_script.exists():
                self.logger.error(f"Исходный Lua скрипт не найден: {source_script}")
                return False
            
            if target_script.exists() and force:
                target_script.unlink()
            
            # Копируем файл
            import shutil
            shutil.copy2(source_script, target_script)
            
            self.logger.info(f"Lua скрипт {'обновлен' if force else 'создан'}: {target_script}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка копирования Lua скрипта: {e}")
            return False

    def _extract_playlist_name(self, playlist_file: Path, playlist_id: int) -> str:
        """Извлекает имя плейлиста из файла или базы данных"""
        try:
            # Сначала пробуем получить имя из базы данных
            try:
                from ..models import Playlist
                playlist = self.db_session.query(Playlist).get(playlist_id)
                if playlist and playlist.name:
                    return playlist.name
            except:
                pass
            
            # Если не удалось, берем имя из файла
            name = playlist_file.stem
            if name.startswith('playlist_'):
                name = name[9:]  # Убираем префикс 'playlist_'
            return name
            
        except Exception as e:
            self.logger.warning(f"Не удалось извлечь имя плейлиста: {e}")
            return f"Плейлист {playlist_id}"

    def _log_available_playlists(self):
        """Логирует доступные M3U файлы"""
        try:
            m3u_files = list(self.m3u_export_dir.glob("*.m3u"))
            if m3u_files:
                self.logger.info(f"Доступные M3U файлы в {self.m3u_export_dir}:")
                for file in m3u_files:
                    self.logger.info(f"  - {file.name}")
            else:
                self.logger.warning(f"Нет M3U файлов в {self.m3u_export_dir}")
        except Exception as e:
            self.logger.warning(f"Не удалось получить список M3U файлов: {e}")

    def play(self, playlist_id: int) -> bool:
        """Запуск слайд-шоу плейлиста"""
        try:
            self.logger.info(f"Запуск слайд-шоу плейлиста ID: {playlist_id}")
            
            # Используем playlist_service для поиска файла
            playlist_file = self.playlist_service.find_playlist_file(
                playlist_id, 
                self.m3u_export_dir
            )
            
            if not playlist_file:
                self._log_available_playlists()
                raise ValueError(f"Файл плейлиста {playlist_id} не найден")
                
            self.logger.info(f"Найден файл плейлиста: {playlist_file}")
            
            if not playlist_file.exists():
                raise ValueError(f"Файл не существует: {playlist_file}")
            
            # Используем m3u_manager для обработки формата
            fixed_playlist_path = self.m3u_manager.ensure_proper_m3u_format(playlist_file)
            
            # Получаем имя плейлиста
            playlist_name = self._extract_playlist_name(playlist_file, playlist_id)
            
            self.logger.info(f"Запуск слайд-шоу: {playlist_name} (файл: {fixed_playlist_path})")
            
            # Обновляем статус в БД
            try:
                self._update_playback_status(playlist_id, 'playing')
            except Exception as e:
                self.logger.warning(f"Не удалось обновить статус в БД: {e}")
            
            # Останавливаем логотип и ждем немного
            self._logo_manager.display_idle_logo()
            time.sleep(0.5)  # Даем время для остановки логотипа
            
            # Останавливаем любые текущие воспроизведения
            self._stop_current_playback()
            time.sleep(0.2)
            
            # Используем mpv_manager для настройки
            self._mpv_manager.configure_for_slideshow()
            
            # Загружаем Lua скрипт если включено
            if self.use_slideshow:
                self._load_lua_script()
            else:
                self.logger.warning("Слайд-шоу отключено в конфигурации")
            
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Проверяем состояние MPV перед загрузкой
            core_idle_result = self._mpv_manager._send_command({
                "command": ["get_property", "core-idle"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            core_idle = True
            if core_idle_result and core_idle_result.get("error") == "success":
                core_idle = core_idle_result.get("data", True)
                self.logger.info(f"Состояние MPV перед загрузкой: core-idle={core_idle}")
            
            # Если MPV в idle режиме, нужно сначала загрузить что-то чтобы "разбудить" его
            if core_idle:
                self.logger.info("MPV в idle режиме, будим его...")
                # Загружаем пустой файл чтобы выйти из idle режима
                self._mpv_manager._send_command({
                    "command": ["loadfile", "/dev/null", "replace"],
                    "request_id": int(time.time() * 1000)
                }, timeout=5.0)
                time.sleep(0.5)
            
            # Загружаем плейлист
            result = self._mpv_manager._send_command({
                "command": ["loadlist", fixed_playlist_path, "replace"],
                "request_id": int(time.time() * 1000)
            }, timeout=10.0)
            
            self.logger.info(f"Результат загрузки плейлиста: {result}")
            
            if not result or result.get("error") != "success":
                self.logger.error(f"Ошибка загрузки плейлиста: {result}")
                raise RuntimeError(f"Не удалось загрузить плейлист")
            
            # Ждем немного чтобы плейлист загрузился
            time.sleep(0.5)
            
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Правильная последовательность запуска
            # 1. Убеждаемся, что не на паузе
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            # 2. Переходим к первому элементу (индекс 0)
            self._mpv_manager._send_command({
                "command": ["playlist-play-index", "0"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            # 3. Принудительно снимаем с паузы еще раз
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            self.logger.info("Принудительный старт воспроизведения")
            
            # Получаем информацию о плейлисте
            playlist_info = self._get_playlist_info()
            
            # Дополнительная проверка через 0.5 секунды
            time.sleep(0.5)
            
            # Проверяем текущую позицию в плейлисте
            position_result = self._mpv_manager._send_command({
                "command": ["get_property", "playlist-pos"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            if position_result and position_result.get("error") == "success":
                position = position_result.get("data", -1)
                if position >= 0:
                    self.logger.info(f"Воспроизведение началось, позиция в плейлисте: {position}")
                else:
                    self.logger.warning(f"Воспроизведение не началось, позиция: {position}")
                    
                    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Альтернативный метод для idle режима
                    # Загружаем первый файл напрямую
                    try:
                        # Получаем список файлов из плейлиста
                        playlist_result = self._mpv_manager._send_command({
                            "command": ["get_property", "playlist"],
                            "request_id": int(time.time() * 1000)
                        }, timeout=2.0)
                        
                        if playlist_result and playlist_result.get("error") == "success":
                            playlist_data = playlist_result.get("data", [])
                            if playlist_data and len(playlist_data) > 0:
                                first_file = playlist_data[0].get("filename")
                                if first_file:
                                    self.logger.info(f"Загружаем первый файл напрямую: {first_file}")
                                    
                                    # Останавливаем текущее
                                    self._mpv_manager._send_command({
                                        "command": ["stop"],
                                        "request_id": int(time.time() * 1000)
                                    }, timeout=2.0)
                                    time.sleep(0.2)
                                    
                                    # Загружаем файл напрямую
                                    self._mpv_manager._send_command({
                                        "command": ["loadfile", first_file, "replace"],
                                        "request_id": int(time.time() * 1000)
                                    }, timeout=5.0)
                                    time.sleep(0.2)
                                    
                                    # Снимаем с паузы
                                    self._mpv_manager._send_command({
                                        "command": ["set_property", "pause", "no"],
                                        "request_id": int(time.time() * 1000)
                                    }, timeout=2.0)
                    except Exception as e:
                        self.logger.warning(f"Не удалось загрузить первый файл напрямую: {e}")
            
            # Уведомляем клиентов
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist_id': playlist_id,
                'playlist_name': playlist_name,
                'type': 'slideshow',
                'info': playlist_info
            })
            
            # Получаем отладочную информацию
            time.sleep(0.5)
            debug_info = self.get_mpv_debug_info()
            self.logger.info(f"Отладочная информация: {debug_info}")
            
            # Проверяем, что воспроизведение действительно началось
            playback_status = self._check_playback_status()
            if not playback_status.get('playing', False):
                self.logger.warning("Воспроизведение может не начаться автоматически, пробуем принудительно")
                self._force_playback_start()
            
            self.logger.info(f"Слайд-шоу '{playlist_name}' успешно запущено")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка запуска слайд-шоу: {str(e)}")
            
            try:
                self._update_playback_status(None, 'error')
            except:
                pass
            
            try:
                self._logo_manager.display_idle_logo()
                self.logger.info("Возврат к логотипу успешен")
            except Exception as logo_error:
                self.logger.error(f"Не удалось показать логотип: {str(logo_error)}")
            
            raise RuntimeError(f"Не удалось запустить слайд-шоу: {str(e)}")

    def _check_playback_status(self) -> Dict[str, bool]:
        """Проверяет статус воспроизведения"""
        try:
            status = {
                'playing': False,
                'paused': False,
                'idle': True
            }
            
            # Проверяем core-idle
            result = self._mpv_manager._send_command({
                "command": ["get_property", "core-idle"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            if result and result.get("error") == "success":
                status['idle'] = result.get("data", True)
            
            # Проверяем паузу
            result = self._mpv_manager._send_command({
                "command": ["get_property", "pause"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            if result and result.get("error") == "success":
                status['paused'] = result.get("data", False)
            
            status['playing'] = not status['idle'] and not status['paused']
            
            return status
            
        except Exception as e:
            self.logger.warning(f"Ошибка проверки статуса воспроизведения: {e}")
            return {'playing': False, 'paused': False, 'idle': True}

    def _force_playback_start(self):
        """Принудительно запускает воспроизведение"""
        try:
            # Снимаем с паузы
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            # Команда idle no
            self._mpv_manager._send_command({
                "command": ["idle", "no"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            # Перезапускаем воспроизведение с позиции 0
            self._mpv_manager._send_command({
                "command": ["seek", "0", "absolute"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            self.logger.info("Воспроизведение принудительно запущено")
            
        except Exception as e:
            self.logger.warning(f"Не удалось принудительно запустить воспроизведение: {e}")

    def _wake_up_mpv_from_idle(self):
        """Выводит MPV из idle режима"""
        try:
            # Проверяем состояние
            result = self._mpv_manager._send_command({
                "command": ["get_property", "core-idle"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            if result and result.get("error") == "success":
                is_idle = result.get("data", True)
                if is_idle:
                    self.logger.info("MPV в idle режиме, пробуем разбудить...")
                    
                    # Способ 1: Загрузить пустой файл
                    self._mpv_manager._send_command({
                        "command": ["loadfile", "/dev/null", "replace"],
                        "request_id": int(time.time() * 1000)
                    }, timeout=5.0)
                    
                    # Способ 2: Команда idle
                    self._mpv_manager._send_command({
                        "command": ["idle", "no"],
                        "request_id": int(time.time() * 1000)
                    }, timeout=2.0)
                    
                    time.sleep(0.3)
                    
                    # Проверяем снова
                    result = self._mpv_manager._send_command({
                        "command": ["get_property", "core-idle"],
                        "request_id": int(time.time() * 1000)
                    }, timeout=2.0)
                    
                    if result and result.get("error") == "success":
                        is_idle = result.get("data", True)
                        self.logger.info(f"После пробуждения: core-idle={is_idle}")
                    
                    return not is_idle
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Ошибка при пробуждении MPV: {e}")
            return False
   
    def _stop_current_playback(self):
        """Останавливает текущее воспроизведение"""
        try:
            # Останавливаем MPV
            self._mpv_manager._send_command({
                "command": ["stop"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            self.logger.info("Текущее воспроизведение остановлено")
        except Exception as e:
            self.logger.warning(f"Не удалось остановить воспроизведение: {e}")
    
    def _load_lua_script(self):
        """Загрузка Lua скрипта в MPV"""
        try:
            # Сначала проверяем, есть ли уже загруженные скрипты
            result = self._mpv_manager._send_command({
                "command": ["script-message", "check-slideshow"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            # Если скрипт уже загружен, не грузим снова
            if result and result.get("error") == "success":
                self.logger.info("Lua скрипт слайд-шоу уже загружен")
                return True
            
            # Загружаем скрипт
            result = self._mpv_manager._send_command({
                "command": ["load-script", str(self.lua_script_path.absolute())],
                "request_id": int(time.time() * 1000)
            }, timeout=5.0)
            
            if result and result.get("error") == "success":
                self.logger.info("Lua скрипт слайд-шоу успешно загружен")
                return True
            else:
                self.logger.warning(f"Не удалось загрузить Lua скрипт: {result}")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка загрузки Lua скрипта: {str(e)}")
            return False
   
    def _get_playlist_info(self) -> Dict:
        """Получает информацию о текущем плейлисте"""
        try:
            result = self._mpv_manager._send_command({
                "command": ["get_property", "playlist-count"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            count = 0
            if result and result.get("error") == "success":
                count = result.get("data", 0)
            
            return {
                'playlist_count': count,
                'status': 'loaded'
            }
            
        except Exception as e:
            self.logger.warning(f"Ошибка получения информации о плейлисте: {e}")
            return {'playlist_count': 0, 'status': 'unknown'}
    
    def _ensure_playback_started(self):
        """Убеждается, что воспроизведение началось"""
        try:
            # Проверяем idle состояние
            result = self._mpv_manager._send_command({
                "command": ["get_property", "core-idle"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            if result and result.get("error") == "success":
                is_idle = result.get("data", True)
                if is_idle:
                    self.logger.warning("MPV в idle состоянии, пытаемся запустить")
                    self._wake_up_mpv_from_idle()
            
            # Проверяем паузу
            result = self._mpv_manager._send_command({
                "command": ["get_property", "pause"],
                "request_id": int(time.time() * 1000)
            }, timeout=2.0)
            
            if result and result.get("error") == "success":
                is_paused = result.get("data", True)
                if is_paused:
                    # Снимаем с паузы
                    self._mpv_manager._send_command({
                        "command": ["set_property", "pause", "no"],
                        "request_id": int(time.time() * 1000)
                    }, timeout=2.0)
                    self.logger.info("Воспроизведение принудительно запущено")
            
        except Exception as e:
            self.logger.warning(f"Ошибка проверки состояния воспроизведения: {e}")
    
    def _update_playback_status(self, playlist_id: Optional[int], status: str):
        """Обновление статуса воспроизведения в БД"""
        try:
            from ..models import PlaybackStatus
            playback = self.db_session.query(PlaybackStatus).first() or PlaybackStatus()
            playback.playlist_id = playlist_id
            playback.status = status
            self.db_session.add(playback)
            self.db_session.commit()
            self.logger.info(f"Статус воспроизведения обновлен: {status} (playlist_id: {playlist_id})")
        except Exception as e:
            self.logger.debug(f"Не удалось обновить статус в БД: {e}")
    
    def get_mpv_debug_info(self) -> Dict:
        """Получение отладочной информации от MPV"""
        try:
            info = {}
            
            props = [
                ("path", "current_file"),
                ("pause", "paused"),
                ("playlist-pos", "playlist_position"),
                ("playlist-count", "playlist_count"),
                ("time-pos", "time_position"),
                ("duration", "duration"),
                ("core-idle", "core_idle"),
                ("vo", "video_output"),
                ("ao", "audio_output")
            ]
            
            for prop, key in props:
                try:
                    response = self._mpv_manager._send_command({
                        "command": ["get_property", prop],
                        "request_id": int(time.time() * 1000)
                    }, timeout=2.0)
                    if response and 'data' in response:
                        info[key] = response['data']
                except:
                    continue
            
            return info
            
        except Exception as e:
            self.logger.error(f"Ошибка получения отладочной информации MPV: {str(e)}")
            return {'error': str(e)}

    def stop(self) -> bool:
        """Остановка воспроизведения"""
        try:
            self.logger.info("Остановка воспроизведения...")
            
            # Обновляем статус
            try:
                self._update_playback_status(None, 'idle')
            except:
                pass
            
            # Останавливаем MPV
            try:
                self._mpv_manager._send_command({
                    "command": ["stop"],
                    "request_id": int(time.time() * 1000)
                }, timeout=2.0)
            except:
                pass
            
            # Возвращаемся к логотипу
            result = self._logo_manager.display_idle_logo()
            
            # Уведомляем клиентов
            self.socketio.emit('playback_state', {
                'status': 'idle',
                'playlist_id': None,
                'playlist_name': None
            })
            
            self.logger.info("Воспроизведение остановлено")
            return result
            
        except Exception as e:
            self.logger.error(f"Ошибка остановки: {str(e)}")
            return False

    def get_status(self) -> Dict:
        """Получение статуса воспроизведения"""
        try:
            status = {
                'status': 'idle',
                'playlist_id': None,
                'playlist_name': None,
                'settings': getattr(self._mpv_manager, '_current_settings', {})
            }
            
            # Пробуем получить статус из БД
            try:
                from ..models import PlaybackStatus
                db_status = self.db_session.query(PlaybackStatus).first()
                if db_status and db_status.playlist_id:
                    status['status'] = db_status.status or 'playing'
                    status['playlist_id'] = db_status.playlist_id
                    
                    # Пробуем получить имя плейлиста
                    try:
                        from ..models import Playlist
                        playlist = self.db_session.query(Playlist).get(db_status.playlist_id)
                        if playlist:
                            status['playlist_name'] = playlist.name
                    except:
                        pass
            except Exception as e:
                self.logger.debug(f"Не удалось получить статус из БД: {e}")
                
            return status
            
        except Exception as e:
            self.logger.error(f"Ошибка получения статуса: {str(e)}")
            return {
                'status': 'error',
                'playlist_id': None,
                'playlist_name': None,
                'settings': {}
            }

    def restart_mpv(self) -> bool:
        """Перезапуск MPV"""
        try:
            self.logger.info("Перезапуск MPV...")
            
            if self._mpv_manager._mpv_process:
                try:
                    self._mpv_manager._mpv_process.terminate()
                    self._mpv_manager._mpv_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.logger.warning("MPV не завершился корректно, принудительная остановка...")
                    self._mpv_manager._mpv_process.kill()
                    self._mpv_manager._mpv_process.wait()
                except Exception as e:
                    self.logger.warning(f"Ошибка остановки MPV: {str(e)}")
            
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    os.unlink(PlaybackConstants.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Ошибка удаления сокета: {str(e)}")
                    try:
                        os.chmod(PlaybackConstants.SOCKET_PATH, 0o777)
                        os.unlink(PlaybackConstants.SOCKET_PATH)
                    except:
                        pass
            
            self._mpv_manager._mpv_ready = False
            self._mpv_manager._socket_ready_event.clear()
            self._mpv_manager._ensure_mpv_service()
            
            try:
                success = self._mpv_manager._wait_for_mpv_ready(timeout=30)
                if success:
                    self.logger.info("MPV успешно перезапущен")
                return success
            except Exception as e:
                self.logger.error(f"Не удалось проверить перезапуск MPV: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка перезапуска MPV: {str(e)}")
            return False

    def get_playback_info(self) -> Dict:
        """Получение информации о воспроизведении"""
        info = {}
        if hasattr(self._mpv_manager, '_current_settings'):
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
        """Остановка логотипа"""
        try:
            res = self._mpv_manager._send_command({
                "command": ["stop"],
                "request_id": int(time.time() * 1000)
            })
            if res is not None:
                self.logger.info("Логотип остановлен")
            else:
                self.logger.warning("Не удалось подтвердить остановку логотипа")
        except Exception as e:
            self.logger.error(f"Ошибка остановки логотипа: {str(e)}")
            
    def restart_idle_logo(self) -> bool:
        """Перезапуск логотипа"""
        return self._logo_manager.restart_idle_logo()
    
    def play_file(self, file_path: str) -> bool:
        """Воспроизведение отдельного файла"""
        try:
            self.logger.info(f"Воспроизведение файла: {file_path}")
            
            if not os.path.exists(file_path):
                raise ValueError(f"Файл не найден: {file_path}")
            
            # Обновляем статус
            try:
                self._update_playback_status(None, 'playing')
            except:
                pass
            
            # Загружаем файл
            result = self._mpv_manager._send_command({
                "command": ["loadfile", file_path, "replace"],
                "request_id": int(time.time() * 1000)
            }, timeout=10.0)
            
            if not result or result.get("error") != "success":
                raise RuntimeError(f"Не удалось загрузить файл: {result}")
            
            # Запускаем воспроизведение
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"],
                "request_id": int(time.time() * 1000)
            })
            
            self.logger.info(f"Файл '{os.path.basename(file_path)}' успешно запущен")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка воспроизведения файла: {str(e)}")
            return False
    
    def test_local_playback(self) -> bool:
        """Тестирование воспроизведения локального файла"""
        try:
            # Ищем любой jpg файл в медиа директории
            jpg_files = list(self.media_root.glob('*.jpg'))
            
            if not jpg_files:
                self.logger.error(f"Не найдены jpg файлы в {self.media_root}")
                return False
            
            # Берем первый файл
            test_file = jpg_files[0]
            self.logger.info(f"Тестируем воспроизведение локального файла: {test_file}")
            
            # Воспроизводим файл
            return self.play_file(str(test_file.absolute()))
            
        except Exception as e:
            self.logger.error(f"Ошибка тестирования локального файла: {str(e)}")
            return False
