import os
import re
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Union, Any
from flask import current_app
from sqlalchemy import func, text
from ..models import Playlist, PlaylistFiles
from .logger import ServiceLogger

class PlaylistService:
    def __init__(self, db_session, logger: Optional[Union[logging.Logger, ServiceLogger]] = None):
        """Инициализация сервиса плейлистов"""
        self.db_session = db_session
        self.logger = logger or ServiceLogger(self.__class__.__name__)

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'module': 'PlaylistService'}
        if extra:
            extra_data.update(extra)
        self.logger.error(message, exc_info=exc_info, extra=extra_data)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'module': 'PlaylistService'}
        if extra:
            extra_data.update(extra)
        self.logger.info(message, extra=extra_data)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'module': 'PlaylistService'}
        if extra:
            extra_data.update(extra)
        self.logger.warning(message, extra=extra_data)

    def _log_debug(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для отладочных логов"""
        extra_data = {'module': 'PlaylistService'}
        if extra:
            extra_data.update(extra)
        self.logger.debug(message, extra=extra_data)

    def _safe_parse_datetime(self, dt_value) -> Optional[datetime]:
        """Безопасный парсер дат с логированием"""
        if dt_value is None:
            return None
        if isinstance(dt_value, datetime):
            return dt_value
        
        try:
            if isinstance(dt_value, str):
                try:
                    return datetime.fromisoformat(dt_value)
                except ValueError:
                    return datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
            elif isinstance(dt_value, (int, float)):
                return datetime.fromtimestamp(dt_value)
            return None
        except Exception as e:
            self._log_warning('Failed to parse datetime', {
                'value': dt_value,
                'error': str(e)
            })
            return None

    def _safe_isoformat(self, dt_value) -> Optional[str]:
        """Безопасное преобразование даты в строку ISO"""
        dt = self._safe_parse_datetime(dt_value)
        return dt.isoformat() if dt else None

    def get_active_playlist(self) -> Optional[Dict]:
        """Получение активного плейлиста"""
        from ..models import PlaybackStatus
        try:
            self._log_debug('Fetching active playlist')
            status = self.db_session.query(PlaybackStatus).first()
            
            if not status or not status.playlist_id:
                self._log_debug('No active playlist found')
                return None

            playlist = self.db_session.query(Playlist).get(status.playlist_id)
            if not playlist:
                self._log_warning('Active playlist not found in database', {
                    'playlist_id': status.playlist_id
                })
                return None

            result = {
                "id": playlist.id,
                "name": playlist.name,
                "status": status.status,
                "files_count": len(playlist.files) if playlist.files else 0
            }
            
            self._log_debug('Active playlist retrieved', {
                'playlist_id': playlist.id
            })
            return result
            
        except Exception as e:
            self._log_error('Failed to get active playlist', {
                'error': str(e)
            })
            return None

    def get_all_playlists(self) -> Dict[str, List[Dict]]:
        """Получение всех плейлистов"""
        try:
            self._log_debug('Fetching all playlists')
            playlists = self.db_session.query(Playlist).order_by(Playlist.last_modified.desc()).all()
            
            result = []
            for playlist in playlists:
                try:
                    result.append({
                        "id": playlist.id,
                        "name": playlist.name,
                        "customer": playlist.customer or "",
                        "files_count": len(playlist.files) if playlist.files else 0,
                        "created_at": self._safe_isoformat(playlist.created_at),
                        "last_modified": self._safe_isoformat(playlist.last_modified)
                    })
                except Exception as e:
                    self._log_warning('Skipping corrupt playlist', {
                        'playlist_id': playlist.id,
                        'error': str(e)
                    })
                    continue
            
            self._log_info('Playlists list retrieved', {
                'count': len(result)
            })
            return {"playlists": result}
            
        except Exception as e:
            self._log_error('Failed to get playlists', {
                'error': str(e)
            })
            raise RuntimeError("Failed to retrieve playlists") from e

    def create_playlist(self, data: Dict) -> Dict[str, int]:
        """Создание нового плейлиста"""
        try:
            self._log_debug('Creating new playlist', {
                'data': {k: v for k, v in data.items() if k != 'files'}  # Не логируем файлы
            })

            if not data or 'name' not in data:
                raise ValueError("Playlist name is required")

            playlist = Playlist(
                name=data['name'],
                customer=data.get('customer', ''),
                created_at=int(time.time()),
                last_modified=int(time.time())
            )
            
            self.db_session.add(playlist)
            self.db_session.commit()
            
            self._log_info('Playlist created successfully', {
                'playlist_id': playlist.id,
                'name': playlist.name
            })
            return {"playlist_id": playlist.id}
            
        except Exception as e:
            self.db_session.rollback()
            self._log_error('Failed to create playlist', {
                'error': str(e),
                'data': {k: v for k, v in data.items() if k != 'files'}
            })
            raise RuntimeError("Failed to create playlist") from e

    def get_playlist(self, playlist_id: int) -> Dict:
        """Получение информации о плейлисте"""
        try:
            self._log_debug('Fetching playlist', {
                'playlist_id': playlist_id
            })

            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            files = []
            for f in playlist.files:
                try:
                    files.append({
                        "id": f.id,
                        "name": f.file_name,
                        "duration": f.duration,
                        "order": f.order,
                        "created_at": self._safe_isoformat(f.created_at)
                    })
                except Exception as e:
                    self._log_warning('Skipping corrupt file', {
                        'file_id': f.id,
                        'playlist_id': playlist_id,
                        'error': str(e)
                    })
                    continue

            result = {
                "id": playlist.id,
                "name": playlist.name,
                "customer": playlist.customer or "",
                "created_at": self._safe_isoformat(playlist.created_at),
                "last_modified": self._safe_isoformat(playlist.last_modified),
                "files": files
            }
            
            self._log_info('Playlist retrieved', {
                'playlist_id': playlist_id,
                'file_count': len(files)
            })
            return result
            
        except Exception as e:
            self._log_error('Failed to get playlist', {
                'playlist_id': playlist_id,
                'error': str(e)
            })
            raise RuntimeError(f"Failed to get playlist {playlist_id}") from e

    def update_playlist(self, playlist_id: int, data: Dict) -> Dict[str, bool]:
        """Обновление информации о плейлисте"""
        try:
            self._log_debug('Updating playlist', {
                'playlist_id': playlist_id,
                'update_data': data
            })

            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            old_name = playlist.name
            
            if 'name' in data:
                playlist.name = data['name']
            if 'customer' in data:
                playlist.customer = data.get('customer', '')
            
            playlist.last_modified = int(time.time())
            self.db_session.commit()

            if 'name' in data and data['name'] != old_name:
                try:
                    self._generate_m3u_for_playlist(playlist, old_name)
                except Exception as m3u_error:
                    self._log_error('M3U update failed', {
                        'playlist_id': playlist_id,
                        'error': str(m3u_error)
                    })

            self._log_info('Playlist updated successfully', {
                'playlist_id': playlist_id
            })
            return {"success": True}
            
        except Exception as e:
            self.db_session.rollback()
            self._log_error('Failed to update playlist', {
                'playlist_id': playlist_id,
                'error': str(e)
            })
            raise RuntimeError(f"Failed to update playlist {playlist_id}") from e

    def _generate_m3u_for_playlist(self, playlist, old_name=None):
        """Генерация M3U файла для плейлиста"""
        try:
            config = current_app.config
            media_url = config['MEDIA_URL']
            export_dir = config['M3U_EXPORT_DIR']
            base_url = config.get('MEDIA_BASE_URL', 'http://localhost').rstrip('/')
            
            m3u_content = "#EXTM3U\n"
            for file in sorted(playlist.files, key=lambda x: x.order):
                if not file.file_name:
                    continue
                
                file_path = os.path.join(config['MEDIA_ROOT'], file.file_name)
                if not os.path.exists(file_path):
                    self._log_warning('Media file not found', {
                        'file_path': file_path,
                        'playlist_id': playlist.id
                    })
                    continue
                
                file_ext = file.file_name.lower().split('.')[-1]
                if file.duration and file_ext in ['jpg', 'jpeg', 'png']:
                    m3u_content += f"#EXTVLCOPT:run-time={file.duration}\n"
                m3u_content += f"{base_url}{media_url}{file.file_name}\n"
        
            # Создаем безопасное имя
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", playlist.name)
            
            # Сохраняем два файла:
            # 1. По имени плейлиста (для пользователя)
            filename_by_name = f"{safe_name}.m3u"
            filepath_by_name = os.path.join(export_dir, filename_by_name)
            
            # 2. По ID (для программы)
            filename_by_id = f"playlist_{playlist.id}.m3u"
            filepath_by_id = os.path.join(export_dir, filename_by_id)
            
            os.makedirs(os.path.dirname(filepath_by_name), exist_ok=True)
            
            # Сохраняем оба файла
            with open(filepath_by_name, 'w', encoding='utf-8') as f:
                f.write(m3u_content)
            
            with open(filepath_by_id, 'w', encoding='utf-8') as f:
                f.write(m3u_content)

            # Удаляем старые файлы если изменилось имя
            if old_name and old_name != playlist.name:
                old_safe_name = re.sub(r'[\\/*?:"<>|]', "_", old_name)
                old_filepath = os.path.join(export_dir, f"{old_safe_name}.m3u")
                if os.path.exists(old_filepath):
                    try:
                        os.remove(old_filepath)
                    except Exception as e:
                        self._log_warning('Failed to remove old M3U file', {
                            'old_filepath': old_filepath,
                            'error': str(e)
                        })

            self._log_info('M3U playlist generated', {
                'playlist_id': playlist.id,
                'filepath_by_name': filepath_by_name,
                'filepath_by_id': filepath_by_id
            })
            return True
            
        except Exception as e:
            self._log_error('M3U generation failed', {
                'playlist_id': playlist.id,
                'error': str(e)
            })
            raise RuntimeError("M3U generation failed") from e

    def delete_playlist(self, playlist_id):
        """Удаление плейлиста"""
        try:
            self._log_debug('Deleting playlist', {
                'playlist_id': playlist_id
            })

            m3u_result = self.delete_playlist_m3u(playlist_id)
            if not m3u_result['success']:
                self._log_warning('M3U deletion warning', {
                    'playlist_id': playlist_id,
                    'message': m3u_result.get('message')
                })

            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                self._log_error('Playlist not found for deletion', {
                    'playlist_id': playlist_id
                })
                return {
                    "success": False,
                    "error": f"Playlist {playlist_id} not found",
                    "deleted_m3u": m3u_result['success']
                }

            self.db_session.delete(playlist)
            self.db_session.commit()
        
            self._log_info('Playlist deleted successfully', {
                'playlist_id': playlist_id,
                'playlist_name': playlist.name
            })
            return {
                "success": True,
                "message": f"Playlist {playlist_id} deleted successfully",
                "deleted_m3u": m3u_result['success'],
                "playlist_name": playlist.name
            }
            
        except Exception as e:
            self.db_session.rollback()
            self._log_error('Failed to delete playlist', {
                'playlist_id': playlist_id,
                'error': str(e)
            })
            return {
                "success": False,
                "error": str(e),
                "deleted_m3u": False
            }

    def delete_playlist_m3u(self, playlist_id):
        """Удаление M3U файла плейлиста"""
        try:
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                return {
                    "success": False,
                    "error": f"Playlist {playlist_id} not found"
                }

            safe_name = re.sub(r'[\\/*?:"<>|]', "_", playlist.name)
            filename = f"{safe_name}.m3u"
            filepath = os.path.join(current_app.config['M3U_EXPORT_DIR'], filename)

            result = {"success": False, "filepath": filepath}
        
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    self._log_info('M3U file deleted', {
                        'playlist_id': playlist_id,
                        'filepath': filepath
                    })
                    result.update({
                        "success": True,
                        "message": "M3U file deleted"
                    })
                except Exception as e:
                    self._log_error('Failed to delete M3U file', {
                        'filepath': filepath,
                        'error': str(e)
                    })
                    result['error'] = str(e)
            else:
                result.update({
                    "success": True,
                    "message": "M3U file not found (no action needed)"
                })
            
            return result
            
        except Exception as e:
            self._log_error('M3U deletion process failed', {
                'playlist_id': playlist_id,
                'error': str(e)
            })
            return {
                "success": False,
                "error": str(e)
            }

    def update_playlist_files(self, playlist_id: int, files_data: List) -> Dict[str, bool]:
        """Обновление файлов плейлиста"""
        try:
            self._log_debug('Updating playlist files', {
                'playlist_id': playlist_id,
                'file_count': len(files_data)
            })

            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            self.db_session.query(PlaylistFiles).filter_by(playlist_id=playlist_id).delete()
        
            for order, file_data in enumerate(files_data, start=1):
                if not file_data.get('file_name'):
                    raise ValueError("Missing required field: file_name")
                
                file_path = os.path.join(current_app.config['MEDIA_ROOT'], file_data['file_name'])
                if not os.path.exists(file_path):
                    raise ValueError(f"File not found: {file_data['file_name']}")
            
                new_file = PlaylistFiles(
                    playlist_id=playlist_id,
                    file_name=file_data['file_name'],
                    duration=file_data.get('duration', 10),
                    order=file_data.get('order', order)
                )
                self.db_session.add(new_file)
        
            playlist.last_modified = int(time.time())
            self.db_session.commit()
        
            try:
                self._generate_m3u_for_playlist(playlist)
            except Exception as m3u_error:
                self._log_error('M3U generation failed after update', {
                    'playlist_id': playlist_id,
                    'error': str(m3u_error)
                })
                return {
                    "success": True,
                    "warning": "Playlist saved but M3U generation failed"
                }
        
            self._log_info('Playlist files updated successfully', {
                'playlist_id': playlist_id,
                'file_count': len(files_data)
            })
            return {"success": True}
            
        except Exception as e:
            self.db_session.rollback()
            self._log_error('Failed to update playlist files', {
                'playlist_id': playlist_id,
                'error': str(e)
            })
            raise RuntimeError(f"Failed to update playlist files {playlist_id}") from e
    
    def reorder_single_item(self, playlist_id: int, item_id: int, new_position: int) -> bool:
        """Изменение порядка элементов плейлиста"""
        try:
            self._log_debug('Reordering playlist item', {
                'playlist_id': playlist_id,
                'item_id': item_id,
                'new_position': new_position
            })

            with self.db_session.begin():
                playlist = self.db_session.query(Playlist).get(playlist_id)
                if not playlist:
                    return False
                
                files = playlist.files_order or []
                
                if item_id not in files:
                    self._log_warning('Item not found in playlist', {
                        'playlist_id': playlist_id,
                        'item_id': item_id
                    })
                    return False
                
                files.remove(item_id)
                files.insert(new_position - 1, item_id)
                
                playlist.files_order = files
                self.db_session.commit()
                
                self._log_info('Playlist item reordered', {
                    'playlist_id': playlist_id,
                    'item_id': item_id,
                    'new_position': new_position
                })
                return True
                
        except Exception as e:
            self._log_error('Failed to reorder playlist item', {
                'playlist_id': playlist_id,
                'item_id': item_id,
                'error': str(e)
            })
            return False
