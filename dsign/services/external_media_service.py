import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse

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

    def sanitize_mpv_http_headers(
        self,
        headers: Optional[Dict[str, Any]],
        *,
        page_url: Optional[str] = None,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Normalize + sanitize HTTP headers for MPV.

        Key goals:
        - avoid duplicated headers with different casing (e.g. Referer + referer)
        - keep a small allowlist to avoid CDN 4xx on browser-ish headers
        - ensure a single canonical Referer (VK/Rutube CDNs often require it)
        - optionally set Origin derived from page_url
        """
        if not isinstance(headers, dict):
            headers = {}

        allow = {"user-agent", "referer", "origin", "accept", "accept-language", "cookie"}
        out_lc: Dict[str, str] = {}
        for k, v in headers.items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v).strip()
            if not ks or not vs:
                continue
            kl = ks.lower()
            if kl not in allow:
                continue
            # Keep first value; do not allow multiple variants of same header.
            out_lc.setdefault(kl, vs)

        # Prefer page URL as referer for VK/Rutube (CDNs reject direct URLs without it).
        if page_url and "referer" not in out_lc:
            out_lc["referer"] = str(page_url)

        # Some CDNs want Origin that matches referer origin.
        if page_url and "origin" not in out_lc:
            try:
                p = urlparse(str(page_url))
                if p.scheme and p.netloc:
                    out_lc["origin"] = f"{p.scheme}://{p.netloc}"
            except Exception:
                pass

        # Provide a safe default accept-language to mimic a real browser if missing.
        out_lc.setdefault("accept-language", "ru,en;q=0.9")
        # Provide a safe default accept.
        out_lc.setdefault("accept", "*/*")

        # Drop only absurdly large cookie blobs (yt-dlp edge cases). VK/Rutube often need the
        # full cookie set for CDN auth; rely on short URL refresh TTL instead of truncating.
        ck = out_lc.get("cookie")
        if ck and len(ck) > 32768:
            out_lc.pop("cookie", None)

        # Convert to conventional header casing for mpv `http-header-fields`.
        canonical = {
            "user-agent": "User-Agent",
            "referer": "Referer",
            "origin": "Origin",
            "accept": "Accept",
            "accept-language": "Accept-Language",
            "cookie": "Cookie",
        }
        out: Dict[str, str] = {}
        for kl, val in out_lc.items():
            out[canonical.get(kl, kl)] = val
        return out

    def _normalize_playback_headers(
        self,
        headers: Optional[Dict[str, Any]],
        *,
        page_url: Optional[str] = None,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """Playback-time alias for `sanitize_mpv_http_headers` (used by `ensure_fresh_playback`)."""
        return self.sanitize_mpv_http_headers(
            headers,
            page_url=page_url,
            stream_url=stream_url,
            provider=provider,
        )

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
        raw = (url or "").strip()
        if not raw:
            return ""

        # Accept "Code for embedding" (<iframe ... src="...">) pasted from VK/Rutube UI.
        m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', raw, flags=re.IGNORECASE)
        if m:
            raw = (m.group(1) or "").strip()

        # Normalize Rutube embed -> canonical video page URL.
        # Example: https://rutube.ru/play/embed/<id>/  -> https://rutube.ru/video/<id>/
        m = re.search(r"rutube\.ru/(?:play/)?embed/([0-9a-f]{16,})", raw, flags=re.IGNORECASE)
        if m:
            vid = m.group(1)
            return f"https://rutube.ru/video/{vid}/"

        # Normalize VK embed -> canonical page URL (best-effort).
        # Example: https://vkvideo.ru/video_ext.php?oid=-..&id=.. -> https://vkvideo.ru/video<oid>_<id>
        m = re.search(r"vkvideo\.ru/video_ext\.php\?([^#]+)", raw, flags=re.IGNORECASE)
        if m:
            qs = m.group(1)
            oid = None
            vid = None
            for part in qs.split("&"):
                if part.startswith("oid="):
                    oid = part.split("=", 1)[1]
                elif part.startswith("id="):
                    vid = part.split("=", 1)[1]
            if oid and vid:
                return f"https://vkvideo.ru/video{oid}_{vid}"

        return raw

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
        prov = str(getattr(row, "provider", "") or "").strip().lower()
        # VK/Rutube CDN URLs are signed (srcIp, expires, sig). Caching them for an hour reuses
        # URLs bound to yt-dlp's egress IP and breaks playback from the Pi (HTTP 400).
        effective_max = max_age_sec
        if prov in ("vkvideo", "rutube"):
            effective_max = min(int(max_age_sec), 90)
        if row.resolved_url and row.resolved_at and (now - int(row.resolved_at)) < effective_max:
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
        headers: Dict[str, Any] = {}
        try:
            headers = dict(row.http_headers or {})
        except Exception:
            headers = {}

        provider = str(getattr(row, "provider", "") or "")
        page_url = str(getattr(row, "url", "") or "")
        safe = self._normalize_playback_headers(
            headers,
            page_url=page_url,
            stream_url=str(url or ""),
            provider=provider,
        )
        return {"url": url, "http_headers": safe}

    def get_cached_thumbnail_path(self, key: str) -> Optional[Path]:
        media_id = self._parse_ext_key(key)
        if media_id is None:
            return None
        p = self._thumb_path_for(media_id)
        return p if p.exists() else None
