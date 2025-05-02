from sqlalchemy import func, text
from typing import Dict, List, Optional
from datetime import datetime
import logging
from flask import current_app
import time
from dsign.models import Playlist

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
        """Обновление информации о плейлисте"""
        from ..models import Playlist

        try:
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            if 'name' in data:
                playlist.name = data['name']
            if 'customer' in data:
                playlist.customer = data.get('customer', '')
            
            playlist.last_modified = int(time.time())
            self.db_session.commit()
            
            return {"success": True}
        except Exception as e:
            self.db_session.rollback()
            self.logger.error(f"Failed to update playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to update playlist {playlist_id}") from e

    def delete_playlist(self, playlist_id: int) -> Dict[str, bool]:
        """Удаление плейлиста"""
        from ..models import Playlist

        try:
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            self.db_session.delete(playlist)
            self.db_session.commit()
            
            return {"success": True}
        except Exception as e:
            self.db_session.rollback()
            self.logger.error(f"Failed to delete playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to delete playlist {playlist_id}") from e

    def update_playlist_files(self, playlist_id: int, files_data: List[Dict]) -> Dict:
        """Обновляет файлы в плейлисте"""
        from ..models import PlaylistFiles
    
        try:
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                return {"success": False, "error": "Playlist not found"}
        
            # Удаляем старые файлы плейлиста
            self.db_session.query(PlaylistFiles).filter_by(playlist_id=playlist_id).delete()
        
            # Добавляем новые файлы
            for idx, file_data in enumerate(files_data, 1):
                if not isinstance(file_data, dict):
                    continue
                
                filename = file_data.get('filename') or file_data.get('name')
                if not filename:
                    continue
                
                is_video = filename.lower().endswith(('.mp4', '.avi', '.mov'))
                playlist_file = PlaylistFiles(
                    playlist_id=playlist_id,
                    file_name=filename,
                    duration=0 if is_video else int(file_data.get('duration', 10)),
                    order=idx
                )
                self.db_session.add(playlist_file)
        
            playlist.last_modified = int(time.time())
            self.db_session.commit()
            return {"success": True, "updated": len(files_data)}
        
        except Exception as e:
            self.db_session.rollback()
            self.logger.error(f"Failed to update playlist files: {str(e)}")
            return {"success": False, "error": str(e)}
    
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
