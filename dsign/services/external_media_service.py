import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import requests


@dataclass(frozen=True)
class ExternalMediaInfo:
    provider: str  # 'rutube' | 'vkvideo' | 'unknown'
    title: Optional[str]
    page_url: str
    thumbnail_url: Optional[str]
    resolved_url: Optional[str]
    http_headers: Optional[Dict[str, str]]


class ExternalMediaService:
    """
    Resolve and cache external media (Rutube / VK Video) so it can be displayed and played without MPV ytdl.
    """

    def __init__(self, db_session, thumbnail_folder: str, logger):
        # normalize db session object (may be db or db.session)
        self.db_session = getattr(db_session, "session", db_session)
        self.logger = logger
        self.thumbnail_folder = Path(thumbnail_folder)
        self.thumbnail_folder.mkdir(parents=True, exist_ok=True)
        self._thumb_dir = self.thumbnail_folder / "external"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------
    # Provider identification
    # -----------------------
    def detect_provider(self, url: str) -> str:
        u = (url or "").strip().lower()
        if "rutube.ru/" in u:
            return "rutube"
        if "vkvideo.ru/" in u or "vk.com/video" in u or "vkvideo.ru/video" in u:
            return "vkvideo"
        return "unknown"

    def normalize_url(self, url: str) -> str:
        return (url or "").strip()

    def _parse_ext_key(self, key: str) -> Optional[int]:
        m = re.match(r"^ext-(\d+)$", (key or "").strip())
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    # -----------------------
    # Metadata resolution
    # -----------------------
    def _yt_dlp_extract(self, url: str) -> Dict[str, Any]:
        """
        Use yt-dlp python package to extract metadata and a playable URL.
        """
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:
            raise RuntimeError("yt-dlp is not installed") from e

        # Keep it conservative for low-power devices: metadata-only, no downloads.
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 10,
            "retries": 2,
            "cachedir": False,
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict):
                raise RuntimeError("yt-dlp returned invalid info")
            # Some extractors return a wrapper with entries.
            if "entries" in info and isinstance(info.get("entries"), list) and info["entries"]:
                entry = info["entries"][0]
                if isinstance(entry, dict):
                    info = entry
            return info

    def resolve_info(self, url: str) -> ExternalMediaInfo:
        url = self.normalize_url(url)
        provider = self.detect_provider(url)

        title = None
        thumb = None
        resolved = None
        http_headers: Optional[Dict[str, str]] = None

        try:
            info = self._yt_dlp_extract(url)
            title = info.get("title") or info.get("fulltitle")
            thumb = info.get("thumbnail")
            # Prefer a direct URL when available.
            resolved = info.get("url")
            # Some extractors populate `formats`.
            if not resolved and isinstance(info.get("formats"), list) and info["formats"]:
                best = None
                for f in info["formats"]:
                    if not isinstance(f, dict):
                        continue
                    # Prefer combined A/V if possible, otherwise take best video.
                    if f.get("vcodec") != "none" and f.get("acodec") != "none":
                        best = f
                        break
                    if best is None and f.get("vcodec") != "none":
                        best = f
                if best:
                    resolved = best.get("url")

            # Some providers require HTTP headers for playback (User-Agent/Referer/Cookie).
            # yt-dlp exposes these in `http_headers`.
            if isinstance(info.get("http_headers"), dict):
                http_headers = {str(k): str(v) for k, v in info["http_headers"].items() if v is not None}
        except Exception as e:
            # Graceful fallback: still store the page URL and show as external media without thumb.
            self.logger.warning(
                "External media metadata resolution failed (fallback to URL only)",
                extra={"url": url, "provider": provider, "error": str(e)},
            )

        return ExternalMediaInfo(
            provider=provider,
            title=title,
            page_url=url,
            thumbnail_url=thumb,
            resolved_url=resolved,
            http_headers=http_headers,
        )

    # -----------------------
    # Thumbnail caching
    # -----------------------
    def _thumb_path_for(self, media_id: int) -> Path:
        return self._thumb_dir / f"ext-{media_id}.jpg"

    def _download_thumb(self, url: str, dest: Path) -> bool:
        try:
            resp = requests.get(url, timeout=10, stream=True)
            if resp.status_code != 200:
                return False
            # limit: 5MB
            max_bytes = 5 * 1024 * 1024
            read = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    read += len(chunk)
                    if read > max_bytes:
                        return False
                    f.write(chunk)
            return dest.exists() and dest.stat().st_size > 0
        except Exception:
            return False

    # -----------------------
    # DB operations
    # -----------------------
    def get_or_create(self, url: str) -> Tuple["ExternalMedia", bool]:
        from dsign.models import ExternalMedia

        url = self.normalize_url(url)
        if not url:
            raise ValueError("URL is required")

        existing = self.db_session.query(ExternalMedia).filter_by(url=url).first()
        if existing:
            return existing, False

        info = self.resolve_info(url)
        now = int(time.time())
        row = ExternalMedia(
            url=info.page_url,
            provider=info.provider,
            title=info.title,
            thumbnail_url=info.thumbnail_url,
            resolved_url=info.resolved_url,
            resolved_at=now if info.resolved_url else None,
            http_headers=info.http_headers,
            last_checked_at=now,
        )
        self.db_session.add(row)
        self.db_session.commit()

        # Best-effort thumbnail download.
        try:
            if info.thumbnail_url:
                dest = self._thumb_path_for(row.id)
                if not dest.exists():
                    self._download_thumb(info.thumbnail_url, dest)
        except Exception:
            pass

        return row, True

    def delete_by_key(self, key: str) -> bool:
        from dsign.models import ExternalMedia

        media_id = self._parse_ext_key(key)
        if media_id is None:
            return False

        row = self.db_session.query(ExternalMedia).get(media_id)
        if not row:
            return False

        # Also remove cached thumbnail (best-effort).
        try:
            p = self._thumb_path_for(media_id)
            if p.exists():
                p.unlink()
        except Exception:
            pass

        self.db_session.delete(row)
        self.db_session.commit()
        return True

    def get_by_key(self, key: str) -> Optional["ExternalMedia"]:
        from dsign.models import ExternalMedia

        media_id = self._parse_ext_key(key)
        if media_id is None:
            return None
        return self.db_session.query(ExternalMedia).get(media_id)

    def ensure_fresh_resolved_url(self, row: "ExternalMedia", max_age_sec: int = 3600) -> str:
        """
        Return a URL that MPV can play without ytdl. Refresh periodically.
        """
        now = int(time.time())
        if row.resolved_url and row.resolved_at and (now - int(row.resolved_at)) < max_age_sec:
            return row.resolved_url

        info = self.resolve_info(row.url)
        row.provider = info.provider
        row.title = info.title or row.title
        row.thumbnail_url = info.thumbnail_url or row.thumbnail_url
        row.last_checked_at = now
        if info.resolved_url:
            row.resolved_url = info.resolved_url
            row.resolved_at = now
        if info.http_headers:
            row.http_headers = info.http_headers

        self.db_session.add(row)
        self.db_session.commit()

        # Cache thumbnail best-effort.
        try:
            if row.thumbnail_url:
                dest = self._thumb_path_for(row.id)
                if not dest.exists():
                    self._download_thumb(row.thumbnail_url, dest)
        except Exception:
            pass

        return row.resolved_url or row.url

    def ensure_fresh_playback(self, row: "ExternalMedia", max_age_sec: int = 3600) -> Dict[str, Any]:
        """
        Return playback details for MPV: {"url": ..., "http_headers": {...}}.
        Refreshes resolved URL + headers periodically.
        """
        url = self.ensure_fresh_resolved_url(row, max_age_sec=max_age_sec)
        headers = {}
        try:
            headers = dict(row.http_headers or {})
        except Exception:
            headers = {}
        return {"url": url, "http_headers": headers}

    def get_cached_thumbnail_path(self, key: str) -> Optional[Path]:
        media_id = self._parse_ext_key(key)
        if media_id is None:
            return None
        p = self._thumb_path_for(media_id)
        return p if p.exists() else None
