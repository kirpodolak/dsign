import os
import json
import time
import subprocess
from typing import Dict, Optional
from pathlib import Path

from .playback_constants import PlaybackConstants
from .playback_utils import PlaybackUtils


class PlaylistManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager, logo_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)  # Это /var/lib/dsign/media
        self.db_session = db_session
        self._mpv_manager = mpv_manager
        self._logo_manager = logo_manager
        self._last_playback_state = {}
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)
        
        # Получаем путь к папке M3U из конфигурации
        try:
            from flask import current_app
            self.m3u_export_dir = Path(current_app.config.get('M3U_EXPORT_DIR', '/home/dsign/dsign/static/playlists'))
            # Получаем путь к медиафайлам
            self.media_root = Path(current_app.config.get('MEDIA_ROOT', '/var/lib/dsign/media'))
        except:
            # По умолчанию, если нет доступа к конфигу
            self.m3u_export_dir = Path('/home/dsign/dsign/static/playlists')
            self.media_root = Path('/var/lib/dsign/media')
        
        # Создаем папки если не существуют
        self.m3u_export_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Папка с M3U плейлистами: {self.m3u_export_dir.absolute()}")
        self.logger.info(f"Папка с медиафайлами: {self.media_root.absolute()}")
        
    def play(self, playlist_id: int) -> bool:
        """Воспроизведение плейлиста по ID"""
        try:
            self.logger.info(f"Запуск воспроизведения плейлиста ID: {playlist_id}")
            
            # Ищем файл плейлиста
            playlist_file = self._find_playlist_file(playlist_id)
            if not playlist_file:
                # Выводим список доступных файлов для отладки
                self._log_available_playlists()
                raise ValueError(f"Файл плейлиста {playlist_id} не найден")
                
            self.logger.info(f"Найден файл плейлиста: {playlist_file}")
            
            # Проверяем существование файла
            if not playlist_file.exists():
                raise ValueError(f"Файл не существует: {playlist_file}")
                
            # Создаем абсолютный путь к файлу
            playlist_path = str(playlist_file.absolute())
            
            self.logger.info(f"Абсолютный путь к плейлисту: {playlist_path}")
            
            # ЛОГИРУЕМ И ИСПРАВЛЯЕМ СОДЕРЖИМОЕ M3U ФАЙЛА
            fixed_playlist_path = self._fix_m3u_paths(playlist_file)
            
            # Пытаемся получить имя плейлиста
            playlist_name = self._extract_playlist_name(playlist_file, playlist_id)
            self.logger.info(f"Запускается плейлист: {playlist_name} (ID: {playlist_id})")
            
            # Обновляем статус воспроизведения
            try:
                self._update_playback_status(playlist_id, 'playing')
            except Exception as e:
                self.logger.warning(f"Не удалось обновить статус в БД: {e}")
            
            # Останавливаем текущее воспроизведение (логотип)
            self._logo_manager.display_idle_logo()
            time.sleep(0.1)  # Небольшая задержка для стабильности
            
            # Загружаем ИСПРАВЛЕННЫЙ плейлист
            result = self._mpv_manager._send_command({
                "command": ["loadlist", fixed_playlist_path, "replace"],
                "request_id": int(time.time() * 1000)
            }, timeout=10.0)
            
            self.logger.info(f"Результат загрузки плейлиста: {result}")
            
            if not result or result.get("error") != "success":
                raise RuntimeError(f"Не удалось загрузить плейлист: {result}")
            
            # Включаем зацикливание
            self._mpv_manager._send_command({
                "command": ["set_property", "loop-playlist", "inf"],
                "request_id": int(time.time() * 1000)
            })
            
            # Запускаем воспроизведение
            self._mpv_manager._send_command({
                "command": ["set_property", "pause", "no"],
                "request_id": int(time.time() * 1000)
            })
            
            # Уведомляем клиентов
            self.socketio.emit('playback_state', {
                'status': 'playing',
                'playlist_id': playlist_id,
                'playlist_name': playlist_name
            })
            
            # Получаем отладочную информацию
            time.sleep(0.5)
            debug_info = self.get_mpv_debug_info()
            self.logger.info(f"Отладочная информация после запуска: {debug_info}")
            
            self.logger.info(f"Плейлист '{playlist_name}' (ID: {playlist_id}) успешно запущен")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка воспроизведения: {str(e)}")
            
            # Обновляем статус на ошибку
            try:
                self._update_playback_status(None, 'error')
            except:
                pass
            
            # Возвращаемся к логотипу
            try:
                self._logo_manager.display_idle_logo()
                self.logger.info("Возврат к логотипу успешен")
            except Exception as logo_error:
                self.logger.error(f"Не удалось показать логотип: {str(logo_error)}")
            
            raise RuntimeError(f"Не удалось запустить воспроизведение: {str(e)}")
    
    def _fix_m3u_paths(self, m3u_file: Path) -> str:
        """Исправление путей в M3U файле (замена URL на локальные пути)"""
        try:
            with open(m3u_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.logger.info(f"Исходное содержимое M3U:\n{content}")
            
            # Заменяем http://localhost/media/ на локальные пути
            import re
            fixed_content = re.sub(
                r'http://localhost/media/([^/\s]+\.(?:jpg|jpeg|png|mp4|avi|mov|mkv))',
                lambda m: str(self.media_root / m.group(1)),
                content
            )
            
            # Если ничего не изменилось, пытаемся другие паттерны
            if fixed_content == content:
                fixed_content = re.sub(
                    r'http://[^/]+/media/([^/\s]+\.(?:jpg|jpeg|png|mp4|avi|mov|mkv))',
                    lambda m: str(self.media_root / m.group(1)),
                    content
                )
            
            # Создаем временный файл с исправленными путями
            temp_file = self.tmp_dir / f"fixed_{m3u_file.name}"
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            
            self.logger.info(f"Исправленное содержимое M3U:\n{fixed_content}")
            
            # Проверяем существование файлов
            lines = fixed_content.split('\n')
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    if os.path.exists(line):
                        self.logger.info(f"Файл существует: {line}")
                    else:
                        self.logger.warning(f"Файл НЕ существует: {line}")
            
            return str(temp_file.absolute())
            
        except Exception as e:
            self.logger.error(f"Ошибка исправления M3U файла: {str(e)}")
            # Если не удалось исправить, используем оригинальный файл
            return str(m3u_file.absolute())
    
    def _extract_playlist_name(self, playlist_file: Path, playlist_id: int) -> str:
        """Извлечение имени плейлиста из файла или БД"""
        # Сначала пробуем из имени файла
        filename = playlist_file.stem
        
        # Если имя файла содержит ID, убираем его для красивого отображения
        import re
        name_without_id = re.sub(rf'playlist_{playlist_id}|list_{playlist_id}|pl_{playlist_id}', '', filename, flags=re.IGNORECASE)
        name_without_id = name_without_id.strip(' _-')
        
        if name_without_id:
            return name_without_id
        
        # Пробуем получить из БД
        try:
            from ..models import Playlist
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if playlist:
                return playlist.name
        except:
            pass
        
        # Если ничего не получилось, возвращаем имя файла
        return filename
    
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
    
    def _log_available_playlists(self):
        """Логирование доступных файлов плейлистов"""
        try:
            self.logger.info("Поиск доступных файлов плейлистов...")
            
            if not self.m3u_export_dir.exists():
                self.logger.warning(f"Папка M3U не существует: {self.m3u_export_dir}")
                return
            
            m3u_files = list(self.m3u_export_dir.glob("*.m3u"))
            
            if m3u_files:
                self.logger.info(f"Найдено {len(m3u_files)} файлов плейлистов:")
                for file_path in m3u_files:
                    self.logger.info(f"  - {file_path.name}")
                    # Показываем первые 3 строки каждого файла
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            lines = [next(f).strip() for _ in range(3) if f]
                            self.logger.info(f"    Начало: {lines}")
                    except:
                        pass
            else:
                self.logger.warning("Не найдено ни одного .m3u файла")
                
        except Exception as e:
            self.logger.error(f"Ошибка поиска файлов плейлистов: {str(e)}")
    
    def _find_playlist_file(self, playlist_id: int) -> Optional[Path]:
        """Поиск файла плейлиста по ID"""
        try:
            self.logger.info(f"Поиск файла плейлиста для ID: {playlist_id}")
            
            possible_names = [
                f"playlist_{playlist_id}.m3u",
                f"list_{playlist_id}.m3u",
                f"pl_{playlist_id}.m3u",
                f"{playlist_id}.m3u",
                f"playlist_{playlist_id}.M3U",
                f"Playlist_{playlist_id}.m3u"
            ]
            
            # Сначала ищем по точным совпадениям
            for name in possible_names:
                file_path = self.m3u_export_dir / name
                if file_path.exists():
                    self.logger.info(f"Найден файл по точному совпадению: {file_path}")
                    return file_path
            
            # Если не нашли, ищем файлы содержащие ID в имени
            import re
            for file_path in self.m3u_export_dir.glob("*.m3u"):
                filename = file_path.stem
                
                if str(playlist_id) in filename:
                    patterns = [
                        rf'playlist_{playlist_id}(?!\d)',
                        rf'list_{playlist_id}(?!\d)',
                        rf'pl_{playlist_id}(?!\d)',
                        rf'^{playlist_id}(?!\d)',
                        rf'_{playlist_id}(?!\d)'
                    ]
                    
                    for pattern in patterns:
                        if re.search(pattern, filename, re.IGNORECASE):
                            self.logger.info(f"Найден файл с ID в имени: {file_path}")
                            return file_path
            
            self.logger.warning(f"Файл плейлиста для ID {playlist_id} не найден")
            return None
            
        except Exception as e:
            self.logger.error(f"Ошибка поиска файла плейлиста: {str(e)}")
            return None

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
                ("core-idle", "core_idle")
            ]
            
            for prop, key in props:
                response = self._mpv_manager._send_command({
                    "command": ["get_property", prop],
                    "request_id": int(time.time() * 1000)
                })
                if response and 'data' in response:
                    info[key] = response['data']
            
            self.logger.info(f"Отладочная информация MPV: {info}")
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
                # Пробуем найти в других местах
                possible_dirs = [
                    self.media_root,
                    Path('/home/dsign/dsign/static/media'),
                    Path('/var/lib/dsign/media'),
                    Path('/tmp')
                ]
                
                for dir_path in possible_dirs:
                    if dir_path.exists():
                        files = list(dir_path.glob('*.jpg'))
                        if files:
                            jpg_files = files
                            self.logger.info(f"Найдены файлы в {dir_path}")
                            break
            
            if not jpg_files:
                self.logger.error("Не найдены jpg файлы ни в одной директории")
                return False
            
            # Берем первый файл
            test_file = jpg_files[0]
            self.logger.info(f"Тестируем воспроизведение локального файла: {test_file}")
            
            # Воспроизводим файл
            return self.play_file(str(test_file.absolute()))
            
        except Exception as e:
            self.logger.error(f"Ошибка тестирования локального файла: {str(e)}")
            return False
