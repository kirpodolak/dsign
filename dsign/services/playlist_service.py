import os
import re
from sqlalchemy import func, text
from typing import Dict, List, Optional
from datetime import datetime
import logging
from flask import current_app
import time
from dsign.models import Playlist, PlaylistFiles

class PlaylistService:
    def __init__(self, db_session, logger=None):
        """Инициализация с сессией базы данных"""
        self.db_session = db_session  # Используем защищенный атрибут
        self.logger = logging.getLogger(__name__)

    def _safe_parse_datetime(self, dt_value) -> Optional[datetime]:
        """Универсальный парсер дат с защитой от ошибок"""
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
            self.logger.warning(f"Failed to parse datetime {dt_value}: {str(e)}")
            return None

    def _safe_isoformat(self, dt_value) -> Optional[str]:
        """Безопасное преобразование даты в строку ISO"""
        dt = self._safe_parse_datetime(dt_value)
        return dt.isoformat() if dt else None

    def get_active_playlist(self) -> Optional[Dict]:
        """Получение активного плейлиста"""
        from ..models import PlaybackStatus, Playlist
        try:
            status = self.db_session.query(PlaybackStatus).first()
            if not status or not status.playlist_id:
                return None

            playlist = self.db_session.query(Playlist).get(status.playlist_id)
            if not playlist:
                return None

            return {
                "id": playlist.id,
                "name": playlist.name,
                "status": status.status,
                "files_count": len(playlist.files) if playlist.files else 0
            }
        except Exception as e:
            self.logger.error(f"Failed to get active playlist: {str(e)}", exc_info=True)
            return None

    def get_all_playlists(self) -> Dict[str, List[Dict]]:
        """Получение всех плейлистов с использованием ORM"""
        from ..models import Playlist
        try:
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
                    self.logger.warning(f"Skipping corrupt playlist {playlist.id}: {str(e)}")
                    continue
            
            return {"playlists": result}
            
        except Exception as e:
            self.logger.error(f"Failed to get playlists: {str(e)}", exc_info=True)
            raise RuntimeError("Failed to retrieve playlists") from e

    def create_playlist(self, data: Dict) -> Dict[str, int]:
        """Создание нового плейлиста"""
        from ..models import Playlist

        try:
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
            
            return {"playlist_id": playlist.id}
        except Exception as e:
            self.db_session.rollback()
            self.logger.error(f"Failed to create playlist: {str(e)}", exc_info=True)
            raise RuntimeError("Failed to create playlist") from e

    def get_playlist(self, playlist_id: int) -> Dict:
        """Получение полной информации о плейлисте"""
        from ..models import Playlist, PlaylistFiles

        try:
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
                    self.logger.warning(f"Skipping corrupt file {f.id}: {str(e)}")
                    continue

            return {
                "id": playlist.id,
                "name": playlist.name,
                "customer": playlist.customer or "",
                "created_at": self._safe_isoformat(playlist.created_at),
                "last_modified": self._safe_isoformat(playlist.last_modified),
                "files": files
            }
        except Exception as e:
            self.logger.error(f"Failed to get playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to get playlist {playlist_id}") from e

    def update_playlist(self, playlist_id: int, data: Dict) -> Dict[str, bool]:
        """Обновление информации о плейлисте с генерацией M3U"""
        from ..models import Playlist

        try:
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

            # Генерация M3U при изменении имени
            if 'name' in data and data['name'] != old_name:
                self._generate_m3u_for_playlist(playlist, old_name)
            
            return {"success": True}
        except Exception as e:
            self.db_session.rollback()
            self.logger.error(f"Failed to update playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to update playlist {playlist_id}") from e

    def _generate_m3u_for_playlist(self, playlist):
        """Генерация M3U файла для плейлиста"""
        try:
            # Получаем настройки из конфига
            config = current_app.config
            media_url = config['MEDIA_URL']
            export_dir = config['M3U_EXPORT_DIR']
            
            # Базовый URL берем из конфига или используем fallback
            base_url = config.get('MEDIA_BASE_URL', 'http://localhost').rstrip('/')
            
            m3u_content = "#EXTM3U\n"
            for file in sorted(playlist.files, key=lambda x: x.order):
                if not file.file_name:
                    continue
                
                # Проверяем существование файла
                file_path = os.path.join(config['MEDIA_ROOT'], file.file_name)
                if not os.path.exists(file_path):
                    self.logger.warning(f"File not found: {file_path}")
                    continue
                
                file_ext = file.file_name.lower().split('.')[-1]
            
                if file.duration and file_ext in ['jpg', 'jpeg', 'png']:
                    m3u_content += f"#EXTVLCOPT:run-time={file.duration}\n"
            
                # Формируем URL к файлу
                m3u_content += f"{base_url}{media_url}{file.file_name}\n"
        
            # Создаем безопасное имя файла
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", playlist.name)
            filename = f"{safe_name}.m3u"
            filepath = os.path.join(export_dir, filename)
        
            # Создаем директорию если не существует
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
            # Записываем файл с явным указанием кодировки
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(m3u_content)
            
            self.logger.info(f"M3U playlist successfully generated: {filepath}")
            return True
        
        except Exception as e:
            self.logger.error(f"M3U generation failed: {str(e)}", exc_info=True)
            raise RuntimeError("M3U generation failed") from e

    def delete_playlist(self, playlist_id):
        """Удаляет плейлист и связанный M3U файл"""
        try:
            # Сначала пытаемся удалить M3U файл
            m3u_result = self.delete_playlist_m3u(playlist_id)
            if not m3u_result['success']:
                current_app.logger.warning(f"M3U deletion warning: {m3u_result.get('message')}")

            # Затем удаляем сам плейлист из БД
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                return {
                    "success": False,
                    "error": f"Playlist {playlist_id} not found",
                    "deleted_m3u": m3u_result['success']
                }

            self.db_session.delete(playlist)
            self.db_session.commit()
        
            return {
                "success": True,
                "message": f"Playlist {playlist_id} deleted successfully",
                "deleted_m3u": m3u_result['success'],
                "playlist_name": playlist.name
            }
        
        except Exception as e:
            current_app.logger.error(f"Error deleting playlist {playlist_id}: {str(e)}", exc_info=True)
            self.db_session.rollback()
            return {
                "success": False,
                "error": str(e),
                "deleted_m3u": False
            }

    def delete_playlist_m3u(self, playlist_id):
        """Удаляет M3U файл для указанного плейлиста"""
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
                    current_app.logger.info(f"Successfully deleted M3U file: {filepath}")
                    result.update({
                        "success": True,
                        "message": "M3U file deleted"
                    })
                except Exception as e:
                    current_app.logger.error(f"Error deleting M3U file {filepath}: {str(e)}")
                    result['error'] = str(e)
            else:
                result.update({
                    "success": True,
                    "message": "M3U file not found (no action needed)"
                })
            
            return result
        
        except Exception as e:
            current_app.logger.error(f"Error in M3U deletion process: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    def update_playlist_files(self, playlist_id: int, files_data: List) -> Dict[str, bool]:
        """Обновление файлов плейлиста с учетом порядка"""
        try:
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            # Удаляем существующие файлы
            self.db_session.query(PlaylistFiles).filter_by(playlist_id=playlist_id).delete()
        
            # Добавляем новые файлы с проверкой
            for order, file_data in enumerate(files_data, start=1):
                if not file_data.get('file_name'):
                    raise ValueError("Missing required field: file_name")
                
                # Проверяем существование файла
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
        
            # Обновляем время модификации
            playlist.last_modified = int(time.time())
            self.db_session.commit()
        
            # Пытаемся сгенерировать M3U (не критично для сохранения плейлиста)
            try:
                self._generate_m3u_for_playlist(playlist)
            except Exception as m3u_error:
                self.logger.error(f"M3U generation failed: {str(m3u_error)}")
                return {
                    "success": True,
                    "warning": "Playlist saved but M3U generation failed"
                }
        
            return {"success": True}
            
        except Exception as e:
            self.db_session.rollback()
            self.logger.error(f"Failed to update playlist files {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to update playlist files {playlist_id}") from e
    
    def reorder_single_item(self, playlist_id: int, item_id: int, new_position: int) -> bool:
        """Перемещает элемент плейлиста на новую позицию"""
        try:
            with self.db_session.begin():
                # Получаем текущий порядок
                playlist = self.db_session.query(Playlist).get(playlist_id)
                if not playlist:
                    return False
                
                files = playlist.files_order or []
                
                # Находим и перемещаем элемент
                if item_id not in files:
                    return False
                
                files.remove(item_id)
                files.insert(new_position - 1, item_id)
                
                # Обновляем порядок
                playlist.files_order = files
                self.db_session.commit()
                return True
                
        except Exception as e:
            self.logger.error(f"Error reordering item: {str(e)}")
            return False
