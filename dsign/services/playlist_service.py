from sqlalchemy import func, text
from typing import Dict, List, Optional
from datetime import datetime
import logging
from flask import current_app
import time

class PlaylistService:
    def __init__(self, db_session, logger: Optional[logging.Logger] = None):
        self.db = db_session
        self.logger = logger or logging.getLogger(__name__)

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
            status = self.db.session.query(PlaybackStatus).first()
            if not status or not status.playlist_id:
                return None

            playlist = self.db.session.query(Playlist).get(status.playlist_id)
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
            playlists = self.db.session.query(Playlist).order_by(Playlist.last_modified.desc()).all()
            
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
            
            self.db.session.add(playlist)
            self.db.session.commit()
            
            return {"playlist_id": playlist.id}
        except Exception as e:
            self.db.session.rollback()
            self.logger.error(f"Failed to create playlist: {str(e)}", exc_info=True)
            raise RuntimeError("Failed to create playlist") from e

    def get_playlist(self, playlist_id: int) -> Dict:
        """Получение полной информации о плейлисте"""
        from ..models import Playlist, PlaylistFiles

        try:
            playlist = self.db.session.query(Playlist).get(playlist_id)
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
            playlist = self.db.session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            if 'name' in data:
                playlist.name = data['name']
            if 'customer' in data:
                playlist.customer = data.get('customer', '')
            
            playlist.last_modified = int(time.time())
            self.db.session.commit()
            
            return {"success": True}
        except Exception as e:
            self.db.session.rollback()
            self.logger.error(f"Failed to update playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to update playlist {playlist_id}") from e

    def delete_playlist(self, playlist_id: int) -> Dict[str, bool]:
        """Удаление плейлиста"""
        from ..models import Playlist

        try:
            playlist = self.db.session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            self.db.session.delete(playlist)
            self.db.session.commit()
            
            return {"success": True}
        except Exception as e:
            self.db.session.rollback()
            self.logger.error(f"Failed to delete playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to delete playlist {playlist_id}") from e

    def update_playlist_files(self, playlist_id: int, files_data: List[Dict]) -> Dict[str, bool]:
        """Обновление файлов в плейлисте с поддержкой file_id"""
        from ..models import Playlist, PlaylistFiles

        try:
            if not isinstance(files_data, list):
                raise ValueError("files_data must be a list")

            self.db.session.begin_nested()

            # Удаляем старые файлы плейлиста
            self.db.session.query(PlaylistFiles).filter(
                PlaylistFiles.playlist_id == playlist_id
            ).delete(synchronize_session=False)

            # Добавляем новые файлы
            for order, file_data in enumerate(files_data, start=1):
                if not isinstance(file_data, dict):
                    raise ValueError(f"Invalid file data at position {order}")
            
                # Поддержка как file_id (из галереи), так и file_name (для обратной совместимости)
                file_id = file_data.get('id')
                file_name = file_data.get('name')
            
                if not file_id and not file_name:
                    raise ValueError(f"File data must contain 'id' or 'name' at position {order}")

                self.db.session.add(PlaylistFiles(
                    playlist_id=playlist_id,
                    file_id=file_id,  # Основной вариант (связь с медиагалереей)
                    file_name=file_name,  # Фолбэк (если file_id нет)
                    duration=int(file_data.get('duration', 0)),
                    order=order,
                    created_at=int(time.time())  # Можно заменить на datetime.utcnow()
                ))

            # Обновляем дату модификации плейлиста
            self.db.session.query(Playlist).filter(
                Playlist.id == playlist_id
            ).update({
                'last_modified': int(time.time())
            }, synchronize_session=False)

            self.db.session.commit()
            return {"success": True}

        except Exception as e:
            self.db.session.rollback()
            self.logger.error(f"Failed to update files in playlist {playlist_id}: {str(e)}", exc_info=True)
            raise RuntimeError(f"Failed to update files in playlist {playlist_id}") from e
    
    def reorder_single_item(self, playlist_id: int, item_id: int, new_position: int) -> bool:
        """Изменение позиции одного элемента"""
        try:
            items = self.db.session.query(PlaylistFiles).filter_by(
                playlist_id=playlist_id
            ).order_by(PlaylistFiles.order).all()

            # Находим перемещаемый элемент
            item = next((x for x in items if x.id == item_id), None)
            if not item:
                raise ValueError("Item not found")

            # Обновляем позиции
            items.remove(item)
            items.insert(new_position - 1, item)

            with self.db.session.begin_nested():
                for idx, item in enumerate(items, start=1):
                    item.order = idx
                    self.db.session.add(item)

            self.db.session.commit()
            return True

        except Exception as e:
            self.db.session.rollback()
            self.logger.error(f"Reorder failed: {str(e)}")
            return False
