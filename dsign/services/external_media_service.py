import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Iterable

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
    def _sanitize_http_headers(self, headers: Optional[Dict[str, Any]], *, page_url: str, provider: str) -> Optional[Dict[str, str]]:
        """
        Keep only a conservative allowlist of headers safe/needed for mpv HTTP.
        Some CDNs reject browser-ish headers (Sec-Fetch-*, etc) with 4xx.
        """
        if not isinstance(headers, dict):
            headers = {}

        allow = {"user-agent", "referer", "referrer", "cookie", "accept", "accept-language"}
        out: Dict[str, str] = {}
        for k, v in headers.items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            if not ks:
                continue
            if ks.lower() not in allow:
                continue
            vs = str(v).strip()
            if not vs:
                continue
            out[ks] = vs

        # Prefer canonical page URL as Referer for providers that require it.
        if provider in ("vkvideo", "rutube") and page_url:
            has_ref = any(k.lower() in ("referer", "referrer") for k in out.keys())
            if not has_ref:
                out["Referer"] = page_url

        return out or None

    def _iter_formats(self, info: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        fmts = info.get("formats")
        if isinstance(fmts, list):
            for f in fmts:
                if isinstance(f, dict):
                    yield f

    def _pick_best_playable_url(self, info: Dict[str, Any]) -> Optional[str]:
        """
        Prefer formats that are actual video streams (HLS/DASH/mp4) and avoid
        preview/thumbnail/storyboard "video" that mpv treats like a still image.
        """
        best_url = None
        best_score = None

        def score(fmt: Dict[str, Any]) -> Optional[float]:
            url = fmt.get("url")
            if not isinstance(url, str) or not url:
                return None

            vcodec = (fmt.get("vcodec") or "").strip()
            acodec = (fmt.get("acodec") or "").strip()
            ext = (fmt.get("ext") or "").strip().lower()
            proto = (fmt.get("protocol") or "").strip().lower()
            format_id = (fmt.get("format_id") or "").strip().lower()
            note = (fmt.get("format_note") or "").strip().lower()

            # Must have video.
            if not vcodec or vcodec == "none":
                return None

            # Reject common non-playback "formats".
            bad_markers = ("storyboard", "thumbnail", "preview", "sprite", "images")
            if any(m in format_id for m in bad_markers) or any(m in note for m in bad_markers):
                return None

            # Avoid mjpeg "video" (often a single JPEG frame stream).
            if vcodec.lower() in ("mjpeg", "png", "gif"):
                return None

            # Prefer stream/container types mpv handles well.
            stream_bonus = 0.0
            if "m3u8" in proto or ext in ("m3u8", "mp4", "mkv", "webm", "ts"):
                stream_bonus += 20.0
            if "dash" in proto:
                stream_bonus += 5.0

            # Prefer formats that include audio, but allow video-only.
            av_bonus = 10.0 if (acodec and acodec != "none") else 0.0

            # Resolution/bitrate signal.
            h = fmt.get("height")
            tbr = fmt.get("tbr")
            try:
                h_val = float(h) if isinstance(h, (int, float)) else 0.0
            except Exception:
                h_val = 0.0
            try:
                tbr_val = float(tbr) if isinstance(tbr, (int, float)) else 0.0
            except Exception:
                tbr_val = 0.0

            return stream_bonus + av_bonus + (h_val / 100.0) + (tbr_val / 1000.0)

        for f in self._iter_formats(info):
            s = score(f)
            if s is None:
                continue
            url = f.get("url")
            if best_score is None or s > best_score:
                best_score = s
                best_url = url

        # Fallback to top-level url if we didn't find a better format.
        if best_url:
            return best_url
        u = info.get("url")
        if isinstance(u, str) and u:
            return u
        return None

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
            resolved = self._pick_best_playable_url(info)

            # Some providers require HTTP headers for playback (User-Agent/Referer/Cookie).
            # yt-dlp exposes these in `http_headers`.
            http_headers = self._sanitize_http_headers(
                info.get("http_headers") if isinstance(info, dict) else None,
                page_url=url,
                provider=provider,
            )
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
            http_headers=http_headers or None,
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

    def ensure_fresh_playback(
        self,
        row: "ExternalMedia",
        max_age_sec: int = 3600,
        *,
        allow_network: bool = True,
    ) -> Dict[str, Any]:
        """
        Return playback details for MPV: {"url": ..., "http_headers": {...}}.
        Refreshes resolved URL + headers periodically.
        """
        # IMPORTANT: on user-initiated "Play", we must not block the request on yt-dlp/network.
        # If allow_network=False, we will use cached resolved_url if present and fall back to page URL.
        if allow_network:
            url = self.ensure_fresh_resolved_url(row, max_age_sec=max_age_sec)
        else:
            url = (getattr(row, "resolved_url", None) or getattr(row, "url", None) or "")
        headers: Dict[str, Any] = {}
        try:
            headers = dict(row.http_headers or {})
        except Exception:
            headers = {}

        # IMPORTANT: sanitize again at playback time.
        # Old DB rows may contain browser-ish headers (Sec-Fetch-*, etc) that can trigger 4xx from CDNs.
        safe_headers = self._sanitize_http_headers(
            headers,
            page_url=str(getattr(row, "url", "") or ""),
            provider=str(getattr(row, "provider", "") or ""),
        )
        return {"url": url, "http_headers": safe_headers or {}}

    def get_cached_thumbnail_path(self, key: str) -> Optional[Path]:
        media_id = self._parse_ext_key(key)
        if media_id is None:
            return None
        p = self._thumb_path_for(media_id)
        return p if p.exists() else None
