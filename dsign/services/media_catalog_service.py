"""
Warehouse catalog helpers: teardown a storage key across playlists, meta, and storage.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from werkzeug.utils import secure_filename

from dsign.models import ExternalMedia, MediaItemMeta

logger = logging.getLogger(__name__)


def _parse_ext_id(key: str) -> Optional[int]:
    if not str(key).startswith("ext-"):
        return None
    try:
        return int(str(key).split("-", 1)[1])
    except (TypeError, ValueError, IndexError):
        return None


def remove_storage_key_everywhere(
    *,
    db_session,
    playlist_service: Any,
    file_service: Any,
    external_media_service: Any,
    storage_key: str,
) -> Dict[str, Any]:
    """
    Удалить медиа-ключ: все вхождения в плейлистах (с order + M3U), media_item_meta,
    файл на диске или строка ExternalMedia.
    """
    key = str(storage_key or "").strip()
    if not key:
        return {"success": False, "error": "empty key"}

    try:
        playlist_service.remove_storage_key_from_all_playlists(key)
        db_session.query(MediaItemMeta).filter(MediaItemMeta.storage_key == key).delete(
            synchronize_session=False
        )

        if key.startswith("ext-"):
            media_id = _parse_ext_id(key)
            if media_id is None:
                db_session.commit()
                return {"success": False, "error": "invalid external key", "key": key}
            row = db_session.get(ExternalMedia, media_id)
            if row:
                if external_media_service and hasattr(external_media_service, "_thumb_path_for"):
                    try:
                        p = external_media_service._thumb_path_for(media_id)
                        if p.exists():
                            p.unlink()
                    except OSError:
                        pass
                db_session.delete(row)
        else:
            safe = secure_filename(key)
            fp = file_service.upload_folder / safe
            try:
                if fp.is_file():
                    fp.unlink()
            except OSError as exc:
                logger.warning("Failed to unlink local media %s: %s", key, exc)

        db_session.commit()
        return {"success": True, "key": key}
    except Exception as exc:
        db_session.rollback()
        logger.exception("remove_storage_key_everywhere failed for %s", key)
        return {"success": False, "error": str(exc), "key": key}
