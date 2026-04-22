import os
import subprocess
import traceback
import time
from threading import Event, Thread
from typing import Dict, Optional, Any
from pathlib import Path

from .playback_constants import PlaybackConstants

class PlaylistManager:
    def __init__(self, logger, socketio, upload_folder, db_session, mpv_manager, logo_manager):
        self.logger = logger
        self.socketio = socketio
        self.upload_folder = Path(upload_folder)
        # `db_session` is inconsistently passed around the codebase:
        # sometimes it's a Flask-SQLAlchemy `db` object, sometimes it's `db.session`.
        # Normalize to a real Session that has `.query()/.add()/.commit()/.rollback()`.
        self.db_session = getattr(db_session, 'session', db_session)
        self._mpv_manager = mpv_manager
        self._logo_manager = logo_manager
        self._last_playback_state = {}
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(exist_ok=True)

        self._play_thread: Optional[Thread] = None
        self._stop_event = Event()
        self._active_playlist_id: Optional[int] = None
        # External media resolver is optional; attached lazily to avoid tight coupling.
        self._external_media_service = None
        # Backoff per media key for unstable/blocked streams to avoid busy looping loadfile.
        # key -> {failures:int, next_try_monotonic:float}
        self._media_backoff: Dict[str, Dict[str, Any]] = {}

    def set_external_media_service(self, service) -> None:
        """Attach external media resolver service (optional)."""
        self._external_media_service = service

    def _resolve_playlist_item_path(self, file_name: str) -> Optional[Dict[str, Any]]:
        """
        Convert a playlist file_name into a playback dict: {path,is_video,duration,muted}.
        Supports both local filenames and synthetic external keys ext-<id>.
        """
        if not file_name:
            return None

        # External media key: ext-<id>
        if str(file_name).startswith("ext-"):
            svc = self._external_media_service
            if not svc:
                # If service isn't attached, fallback to the raw key (will fail to loadfile).
                return {"path": str(file_name), "is_video": True}
            row = svc.get_by_key(str(file_name))
            if not row:
                return None
            # Always re-resolve on play: CDN URLs are signed to yt-dlp's egress; cached URLs break on the Pi.
            pb = svc.ensure_fresh_playback(row, max_age_sec=0)
            return {
                "key": str(file_name),
                "path": pb.get("url") or row.resolved_url or row.url,
                "is_video": True,
                "http_headers": pb.get("http_headers") or {},
                "page_url": str(getattr(row, "url", "") or ""),
                "provider": str(getattr(row, "provider", "") or ""),
            }

        # Local file
        file_path = self.upload_folder / str(file_name)
        if not file_path.exists():
            return None
        ext = file_path.suffix.lower()
        is_video = ext in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
        return {"path": str(file_path), "is_video": is_video}

    def _wait_for_mpv_loaded_path(self, expected_path: str, timeout: float = 10.0) -> bool:
        """
        Wait until MPV reports the expected `path` as loaded.

        We intentionally start the "virtual timer" after MPV has actually switched to the file,
        otherwise slow IO/decoding makes per-item durations feel random.
        """
        if not expected_path:
            return False

        deadline = time.monotonic() + max(0.1, float(timeout))
        last_seen = None
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            try:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", "path"]},
                    timeout=2.0,
                )
                if resp and resp.get("error") == "success":
                    last_seen = resp.get("data")
                    if last_seen == expected_path:
                        return True
            except Exception:
                pass
            # small poll interval; keep it low but not busy-loop
            time.sleep(0.1)

        self.logger.debug(
            "MPV did not confirm loaded path within timeout",
            extra={"expected_path": expected_path, "last_seen_path": last_seen, "timeout_sec": timeout},
        )
        return False

    def _wait_for_mpv_vo_configured(self, timeout: float = 5.0) -> bool:
        """
        Wait until MPV reports `vo-configured=true`.

        On DRM/KMS, `path` may switch before the frame is actually presented. Waiting for
        vo-configured helps reduce visible flicker / "blink" between images.
        """
        deadline = time.monotonic() + max(0.1, float(timeout))
        last_val = None
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            try:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", "vo-configured"]},
                    timeout=2.0,
                )
                if resp and resp.get("error") == "success":
                    last_val = resp.get("data")
                    if last_val is True:
                        return True
            except Exception:
                pass
            time.sleep(0.1)
        self.logger.debug(
            "MPV vo-configured did not become true within timeout",
            extra={"timeout_sec": timeout, "last_value": last_val},
        )
        return False

    def _sleep_until(self, deadline_monotonic: float, step: float = 0.2) -> bool:
        """Sleep until deadline or stop event; returns True if reached deadline."""
        step = max(0.05, float(step))
        while True:
            if self._stop_event.is_set():
                return False
            now = time.monotonic()
            remaining = deadline_monotonic - now
            if remaining <= 0:
                return True
            self._stop_event.wait(timeout=min(step, remaining))

    def _mpv_get_prop_bool(self, name: str, timeout: float = 3.0) -> Optional[bool]:
        """Return bool property value, or None if unavailable / error."""
        try:
            resp = self._mpv_manager._send_command(
                {"command": ["get_property", name]},
                timeout=timeout,
            )
        except Exception:
            return None
        if not resp or resp.get("error") != "success":
            return None
        data = resp.get("data")
        if isinstance(data, bool):
            return data
        return None

    def _mpv_get_prop_number(self, name: str, timeout: float = 3.0) -> Optional[float]:
        """Return numeric property (time-pos, duration, ...), or None if unavailable."""
        try:
            resp = self._mpv_manager._send_command(
                {"command": ["get_property", name]},
                timeout=timeout,
            )
        except Exception:
            return None
        if not resp or resp.get("error") != "success":
            return None
        data = resp.get("data")
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            return float(data)
        return None

    def _mpv_get_prop_string(self, name: str, timeout: float = 3.0) -> Optional[str]:
        try:
            resp = self._mpv_manager._send_command(
                {"command": ["get_property", name]},
                timeout=timeout,
            )
        except Exception:
            return None
        if not resp or resp.get("error") != "success":
            return None
        data = resp.get("data")
        if data is None:
            return None
        return str(data)

    def _normalize_mpv_http_headers(
        self,
        headers: Any,
        *,
        page_url: Optional[str] = None,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Normalize/sanitize HTTP headers before passing to MPV.

        Goals:
        - Avoid duplicate headers like `referer` + `Referer` (some CDNs return 400).
        - Keep a small allowlist (CDNs may reject browser-only headers like Sec-Fetch-*).
        - Ensure a single, correct Referer/Origin for VK/Rutube.
        """
        if not isinstance(headers, dict):
            headers = {}

        allow = {
            "user-agent",
            "referer",
            "referrer",
            "origin",
            "cookie",
            "accept",
            "accept-language",
        }

        # Canonicalize keys (Title-Case for readability; MPV doesn't care).
        out: Dict[str, str] = {}
        for k, v in headers.items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v).strip()
            if not ks or not vs:
                continue
            if ks.lower() not in allow:
                continue
            canon = "-".join([p.capitalize() for p in ks.lower().split("-")])
            out[canon] = vs

        # Ensure a single Referer.
        #
        # IMPORTANT: Provider CDNs change behavior over time. Empirically:
        # - VK/OKCDN requires page-level Referer/Origin.
        # - Rutube `river-*.rutube.ru` / `*.rtbcdn.ru` often expects Rutube page-level Referer/Origin
        #   (matches browser/curl behavior), so do not suppress it here. Prefer the ExternalMediaService
        #   sanitizer when attached for a single source of truth.
        prov_lc = (str(provider or "").strip().lower()) if provider is not None else ""
        su = str(stream_url or "")
        is_rutube_cdn = ("rutube" in prov_lc) and (
            "river-" in su or ".rtbcdn.ru/" in su or "rtbcdn.ru/" in su
        )
        ref = out.get("Referer") or out.get("Referrer") or out.get("referer")
        if not ref and page_url:
            ref = page_url
        if ref:
            out.pop("Referrer", None)
            out["Referer"] = str(ref)

        # Derive Origin from referer if not provided.
        if "Origin" not in out:
            base = out.get("Referer") or page_url
            if base and isinstance(base, str) and base.startswith(("http://", "https://")):
                try:
                    from urllib.parse import urlparse

                    p = urlparse(base)
                    if p.scheme and p.netloc:
                        out["Origin"] = f"{p.scheme}://{p.netloc}"
                except Exception:
                    pass

        # Set Accept defaults (some CDNs are picky; keep it minimal).
        out.setdefault("Accept", "*/*")
        out.setdefault("Accept-Language", "ru,en;q=0.9")

        # Ensure we always have a UA (MPV default can be too generic).
        out.setdefault(
            "User-Agent",
            "Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # Never send an empty Cookie header.
        if out.get("Cookie", "").strip() == "":
            out.pop("Cookie", None)
        ck = out.get("Cookie")
        if ck and len(ck) > 32768:
            out.pop("Cookie", None)

        return out

    def _sanitize_headers_for_mpv(
        self,
        headers: Any,
        *,
        page_url: Optional[str] = None,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """Prefer ExternalMediaService sanitizer when attached (single source of truth)."""
        svc = self._external_media_service
        if svc and hasattr(svc, "sanitize_mpv_http_headers"):
            try:
                return svc.sanitize_mpv_http_headers(
                    headers,
                    page_url=page_url,
                    stream_url=stream_url,
                    provider=provider,
                )
            except Exception:
                pass
        return self._normalize_mpv_http_headers(
            headers,
            page_url=page_url,
            stream_url=stream_url,
            provider=provider,
        )

    def _clear_mpv_http_options(self) -> None:
        """Reset per-stream HTTP options so a previous item cannot poison the next load."""
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "http-header-fields", ""]},
                timeout=3.0,
            )
        except Exception:
            pass
        # Some network fetches (notably EDL sources opened by lavf/ffmpeg) do not always honor
        # `http-header-fields`. Clear/override the more specific stream options too.
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "file-local-options/stream-lavf-o", {}]},
                timeout=3.0,
            )
        except Exception:
            pass
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "user-agent", ""]},
                timeout=3.0,
            )
        except Exception:
            pass
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "referrer", ""]},
                timeout=3.0,
            )
        except Exception:
            pass
        # ytdl options can also be sticky across items; clear them for deterministic behavior.
        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "ytdl-format", ""]},
                timeout=3.0,
            )
        except Exception:
            pass

    def _apply_mpv_stream_lavf_options(
        self,
        headers: Dict[str, str],
        *,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> None:
        """
        Apply headers for lavf/ffmpeg-based network opens.

        When mpv plays `edl://` sources from ytdl_hook, the underlying open can go through
        lavf/ffmpeg. Those opens may ignore `http-header-fields`, so we also set
        `file-local-options/stream-lavf-o` (plus `user-agent`/`referrer`) for best compatibility.

        IMPORTANT: Avoid duplicating UA/Referer between multiple mechanisms when possible.
        """
        if not headers:
            return
        ua = str(headers.get("User-Agent") or "").strip()
        ref = str(headers.get("Referer") or "").strip()
        cookie = str(headers.get("Cookie") or "").strip()
        if not ua and not ref and not cookie:
            return

        # ffmpeg expects CRLF-separated header lines in `headers`.
        #
        # IMPORTANT:
        # If we set the dedicated lavf `cookies` option, avoid also embedding a `Cookie:` header
        # in the generic `headers` blob. Some CDNs respond with HTTP 400 to duplicate Cookie headers.
        hdr_lines = []
        # Rutube CDN seems particularly sensitive to duplicated request context. For this path,
        # prefer the dedicated lavf `referer` option and avoid also sending `Referer:` via `headers`.
        try:
            prov_lc_hdr = (str(provider or "").strip().lower()) if provider is not None else ""
            su_hdr = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            is_rutube_cdn_hdr = (prov_lc_hdr == "rutube") and (
                "river-" in su_hdr or ".rtbcdn.ru/" in su_hdr or "rtbcdn.ru/" in su_hdr
            )
        except Exception:
            is_rutube_cdn_hdr = False
        for k, v in headers.items():
            if not k or not v:
                continue
            lk = str(k).strip().lower()
            if lk == "cookie" and cookie:
                continue
            if is_rutube_cdn_hdr and lk in ("referer", "referrer", "origin"):
                # `referer` is already provided via the dedicated option; Origin is not required for ffmpeg.
                continue
            # We'll pass UA/Referer both in dedicated fields and in `headers` when present.
            hdr_lines.append(f"{str(k).strip()}: {str(v).strip()}")
        hdr_blob = "\r\n".join([h for h in hdr_lines if h])

        opts: Dict[str, str] = {}
        if ua:
            opts["user_agent"] = ua
        if ref:
            # ffmpeg uses `referer` (single-r).
            opts["referer"] = ref
        if cookie:
            # ffmpeg/lavf accepts a cookie string via the `cookies` option; this can be more reliable
            # than relying only on `headers` for some CDNs.
            # Do not append terminators: some CDNs reject malformed cookie strings.
            opts["cookies"] = cookie

        # Rutube CDN: force HTTP/1.1. Empirically, curl/ffmpeg repros succeed with HTTP/1.1, while
        # mpv-embedded ffmpeg opens can differ in negotiation and trigger HTTP 400.
        try:
            prov_lc = (str(provider or "").strip().lower()) if provider is not None else ""
            su = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            is_rutube_cdn = (prov_lc == "rutube") and (
                "river-" in su or ".rtbcdn.ru/" in su or "rtbcdn.ru/" in su
            )
            if is_rutube_cdn:
                opts.setdefault("http_version", "1.1")
        except Exception:
            pass
        if hdr_blob:
            opts["headers"] = hdr_blob + "\r\n"

        try:
            self._mpv_manager._send_command(
                {"command": ["set_property", "file-local-options/stream-lavf-o", opts]},
                timeout=5.0,
            )
        except Exception:
            pass
        # These are mpv-level fallbacks; they can help non-lavf opens too.
        if ua:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "user-agent", ua]},
                    timeout=3.0,
                )
            except Exception:
                pass
        if ref:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "referrer", ref]},
                    timeout=3.0,
                )
            except Exception:
                pass

    def _merge_mpv_lavf_options(self, base: Any, extra: Dict[str, str]) -> Dict[str, str]:
        """Merge dict-like lavf options, normalizing keys/values as strings."""
        out: Dict[str, str] = {}
        if isinstance(base, dict):
            for k, v in base.items():
                if k is None or v is None:
                    continue
                ks = str(k).strip()
                vs = str(v).strip()
                if not ks or not vs:
                    continue
                out[ks] = vs
        for k, v in (extra or {}).items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v).strip()
            if not ks or not vs:
                continue
            out[ks] = vs
        return out

    def _apply_mpv_lavf_headers_after_ytdl_hook(
        self,
        *,
        normalized_headers: Dict[str, str],
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
        timeout_sec: float = 8.0,
    ) -> None:
        """
        ytdl_hook often sets `file-local-options/stream-lavf-o` (cookies) *after* loadfile.
        That can overwrite our earlier lavf options. Re-apply/merge UA+Referer+headers once the
        cookies option appears so ffmpeg opens (EDL sources) have the right request context.
        """
        if not normalized_headers:
            return
        ua = str(normalized_headers.get("User-Agent") or "").strip()
        ref = str(normalized_headers.get("Referer") or "").strip()
        cookie = str(normalized_headers.get("Cookie") or "").strip()
        if not ua and not ref and not cookie:
            return

        # Build the extra dict we want to ensure exists in stream-lavf-o.
        #
        # If we also set the dedicated lavf `cookies` option, avoid putting `Cookie:` into the
        # generic `headers` blob to prevent duplicate Cookie headers (can trigger HTTP 400).
        hdr_lines = []
        for k, v in normalized_headers.items():
            if not k or not v:
                continue
            lk = str(k).strip().lower()
            if lk in ("user-agent", "referer", "referrer"):
                continue
            if lk == "cookie" and cookie:
                continue
            hdr_lines.append(f"{str(k).strip()}: {str(v).strip()}")
        hdr_blob = "\r\n".join([h for h in hdr_lines if h])
        extra: Dict[str, str] = {}
        if ua:
            extra["user_agent"] = ua
        if ref:
            extra["referer"] = ref
        if cookie:
            # Do not append terminators: some CDNs reject malformed cookie strings.
            extra["cookies"] = cookie

        # Same Rutube transport tweak as in the initial lavf set.
        try:
            prov_lc = (str(provider or "").strip().lower()) if provider is not None else ""
            su = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            is_rutube_cdn = (prov_lc == "rutube") and (
                "river-" in su or ".rtbcdn.ru/" in su or "rtbcdn.ru/" in su
            )
            if is_rutube_cdn:
                extra.setdefault("http_version", "1.1")
        except Exception:
            pass
        if hdr_blob:
            extra["headers"] = hdr_blob + "\r\n"

        # Wait briefly for ytdl_hook to populate cookies, then merge+set.
        deadline = time.monotonic() + max(0.2, float(timeout_sec))
        base_val: Any = None
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                resp = self._mpv_manager._send_command(
                    {"command": ["get_property", "file-local-options/stream-lavf-o"]},
                    timeout=2.0,
                )
                if resp and resp.get("error") == "success":
                    base_val = resp.get("data")
                    # If cookies already exist, this is the most common ytdl_hook write point.
                    if isinstance(base_val, dict) and base_val.get("cookies"):
                        break
            except Exception:
                pass
            self._stop_event.wait(timeout=0.15)

        merged = self._merge_mpv_lavf_options(base_val, extra)
        try:
            # Log with a string message + extras; some logging pipelines drop dict-as-message.
            self.logger.info(
                "Reapplying stream-lavf-o after ytdl_hook",
                extra={
                    "event": "mpv_lavf_reapply_after_ytdl_hook",
                    "lavf_keys": sorted(list(merged.keys()))[:25],
                    "has_cookies": bool(isinstance(merged, dict) and merged.get("cookies")),
                    "has_user_agent": bool(isinstance(merged, dict) and merged.get("user_agent")),
                    "has_referer": bool(isinstance(merged, dict) and merged.get("referer")),
                    "has_headers": bool(isinstance(merged, dict) and merged.get("headers")),
                },
            )
            self._mpv_manager._send_command(
                {"command": ["set_property", "file-local-options/stream-lavf-o", merged]},
                timeout=5.0,
            )
        except Exception:
            pass
        # Also keep mpv fallbacks in sync.
        if ua:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "user-agent", ua]},
                    timeout=3.0,
                )
            except Exception:
                pass
        if ref:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "referrer", ref]},
                    timeout=3.0,
                )
            except Exception:
                pass

    def _apply_mpv_ytdl_options(self, item: dict, *, stream_url: str) -> None:
        """
        Some providers (notably VK) frequently resolve to separate DASH streams when using the
        default ytdl format selection. That can result in an `edl://` item that opens and then
        immediately ends (black screen + rapid on_after_end_file).

        Prefer `best` for VK to bias towards a single playable muxed stream (HLS/MP4) when available.
        """
        try:
            provider = str(item.get("provider") or "").strip().lower()
        except Exception:
            provider = ""
        try:
            url = str(stream_url or "")
        except Exception:
            url = ""

        if provider == "vkvideo" or "vkvideo.ru" in url or "vk.com/video" in url:
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "ytdl-format", "best"]},
                    timeout=5.0,
                )
            except Exception:
                pass

    def _apply_mpv_http_headers(self, item: dict, *, stream_url: str) -> Dict[str, str]:
        """
        Set MPV HTTP options for one playlist item. Always clears stale options first.
        Returns the normalized header dict (may be empty).
        """
        http_headers = item.get("http_headers") or {}

        page_url = item.get("page_url")
        if page_url is not None:
            page_url = str(page_url) if page_url else None
        provider = item.get("provider")
        if provider is not None:
            provider = str(provider) if provider else None

        # If the DB row doesn't have captured http_headers (common for older external-media rows),
        # still synthesize a minimal set for network loads from page_url. VK/OKCDN is very sensitive
        # to Referer; without it ffmpeg opens can return HTTP 400 even when cookies exist.
        is_network_url = isinstance(stream_url, str) and stream_url.startswith(
            ("http://", "https://", "ytdl://")
        )
        if not isinstance(http_headers, dict):
            http_headers = {}
        if not http_headers and not is_network_url:
            self._clear_mpv_http_options()
            return {}

        normalized = self._sanitize_headers_for_mpv(
            http_headers,
            page_url=page_url,
            stream_url=stream_url,
            provider=provider,
        )
        self._clear_mpv_http_options()

        # For network streams opened via lavf/ffmpeg (incl. direct Rutube river/rtbcdn URLs),
        # also apply `stream-lavf-o` so ffmpeg uses the same request context as mpv.
        is_network = isinstance(stream_url, str) and stream_url.startswith(("http://", "https://", "ytdl://"))
        if is_network and normalized:
            self._apply_mpv_stream_lavf_options(
                normalized,
                stream_url=stream_url,
                provider=provider,
            )

        try:
            header_lines = []
            for k, v in normalized.items():
                if k is None or v is None:
                    continue
                ks = str(k).strip()
                vs = str(v).strip()
                if not ks or not vs:
                    continue
                header_lines.append(f"{ks}: {vs}")
            if header_lines:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "http-header-fields", "\r\n".join(header_lines)]},
                    timeout=5.0,
                )
            # For VK/OKCDN we prefer to keep UA/Referer here too: ytdl_hook may overwrite
            # lavf options later (cookies-only), but it won't clobber http-header-fields.
        except Exception:
            pass
        return normalized

    def _wait_mpv_leave_idle(self, timeout_sec: float = 45.0) -> bool:
        """
        After loadfile, mpv often reports idle-active=true until the demuxer opens.
        Our old loop treated idle as EOF and skipped network streams in ~1 tick.
        """
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            idle = self._mpv_get_prop_bool("idle-active", timeout=3.0)
            if idle is False:
                return True
            self._stop_event.wait(timeout=0.15)
        return False

    def _wait_mpv_stream_ready(
        self,
        expected_url: str,
        timeout_sec: float = 90.0,
        poll_sec: float = 0.5,
    ) -> bool:
        """
        Some mpv builds never expose `eof-reached` for network streams (always 'property unavailable').
        Detect that demuxer/decoder actually started using time-pos / duration.

        For `ytdl://` items, MPV can spend a noticeable amount of time in the ytdl_hook resolution
        subprocess, and `time-pos`/`duration` may remain unavailable until after `path` switches
        from the virtual `ytdl://...` URL to a real stream URL. In that case, treat `path` switching
        away from `ytdl://` as progress/readiness.
        """
        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        exp = (expected_url or "").strip()
        is_ytdl = exp.startswith("ytdl://")
        # For ytdl:// resolution on low-power devices, be gentle with IPC:
        # - ytdl_hook may take >10s
        # - mpv may update stream-open-filename earlier than time-pos/duration
        heavy_every = 2 if is_ytdl else 1
        tick = 0

        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            if is_ytdl:
                # When ytdl_hook resolves, MPV's `path` often switches to the resolved URL first,
                # while time-pos/duration are still unavailable. Use that as readiness signal.
                cur_stream = self._mpv_get_prop_string("stream-open-filename", timeout=2.0)
                if cur_stream and not str(cur_stream).startswith("ytdl://"):
                    return True
                cur_path = self._mpv_get_prop_string("path", timeout=2.0)
                if cur_path and not str(cur_path).startswith("ytdl://"):
                    return True
            else:
                # For already-resolved `edl://` items we still want to see that MPV has opened
                # something non-empty. Without this, VK can look "ready" and then instantly EOF.
                cur_stream = self._mpv_get_prop_string("stream-open-filename", timeout=2.0)
                cur_path = self._mpv_get_prop_string("path", timeout=2.0)
                if (cur_stream and len(str(cur_stream)) > 0) or (cur_path and len(str(cur_path)) > 0):
                    # do not early-return; keep probing time-pos/duration below
                    pass
            tick += 1
            if heavy_every > 1 and (tick % heavy_every) != 0:
                self._stop_event.wait(timeout=max(0.15, float(poll_sec)))
                continue
            tp = self._mpv_get_prop_number("time-pos", timeout=2.0)
            if tp is not None:
                return True
            dur = self._mpv_get_prop_number("duration", timeout=2.0)
            if dur is not None and dur > 0:
                return True
            self._stop_event.wait(timeout=max(0.1, float(poll_sec)))
        return False

    def _detect_mpv_instant_eof(self, *, window_sec: float = 2.5) -> bool:
        """
        Detect a pathological case where MPV opens a file/stream and immediately returns to idle.
        This happens with some VK `edl://` selections (separate streams / blocked URL) and should
        be treated as a start failure (so we backoff instead of busy-looping).
        """
        deadline = time.monotonic() + max(0.2, float(window_sec))
        saw_not_idle = False
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            idle = self._mpv_get_prop_bool("idle-active", timeout=2.0)
            if idle is False:
                saw_not_idle = True
            if saw_not_idle and idle is True:
                return True
            self._stop_event.wait(timeout=0.1)
        return False

    def _wait_mpv_video_end(
        self,
        playlist_id: int,
        *,
        is_network: bool,
        stream_ready: bool,
        poll_sec: float = 1.0,
    ) -> None:
        """
        End-of-playback detection that works when `eof-reached` is permanently unavailable.

        - Prefer eof-reached == True when MPV returns a real boolean.
        - Else for streams: after stream_ready, treat return to idle-active as end-of-file.
        - Else for local files: same idle fallback after a short grace period.
        """
        start = time.time()
        grace_until = time.monotonic() + (1.5 if is_network else 0.3)
        consecutive_idle = 0

        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            if time.time() - start > 6 * 3600:
                break

            eof = self._mpv_get_prop_bool("eof-reached", timeout=3.0)
            if eof is True:
                break

            idle = self._mpv_get_prop_bool("idle-active", timeout=3.0)
            if idle is True and time.monotonic() >= grace_until:
                if is_network:
                    if stream_ready:
                        consecutive_idle += 1
                        if consecutive_idle >= 2:
                            break
                else:
                    consecutive_idle += 1
                    if consecutive_idle >= 2:
                        break
            else:
                consecutive_idle = 0

            core_idle = self._mpv_get_prop_bool("core-idle", timeout=3.0)
            if (
                eof is False
                and core_idle is True
                and idle is True
                and time.monotonic() >= grace_until
                and (stream_ready or not is_network)
            ):
                break

            self._stop_event.wait(timeout=max(0.2, float(poll_sec)))

    def _log_mpv_network_debug_snapshot(self, *, media_key: str, url: str) -> None:
        """
        Best-effort debug snapshot for stubborn network streams.
        This helps distinguish 'mpv never tried to open URL' vs 'opened but blocked (403/TLS)'.
        """
        try:
            props = ["path", "stream-open-filename", "media-title", "file-format", "demuxer"]
            snap: Dict[str, Any] = {}
            for p in props:
                try:
                    r = self._mpv_manager._send_command({"command": ["get_property", p]}, timeout=2.0)
                    if r and r.get("error") == "success":
                        snap[p] = r.get("data")
                except Exception:
                    pass
            idle = self._mpv_get_prop_bool("idle-active", timeout=2.0)
            core_idle = self._mpv_get_prop_bool("core-idle", timeout=2.0)
            tp = self._mpv_get_prop_number("time-pos", timeout=2.0)
            dur = self._mpv_get_prop_number("duration", timeout=2.0)
            self.logger.warning(
                "MPV network stream debug snapshot",
                extra={
                    "media_key": media_key,
                    "url_preview": str(url)[:160],
                    "idle_active": idle,
                    "core_idle": core_idle,
                    "time_pos": tp,
                    "duration": dur,
                    "mpv_props": snap,
                },
            )
        except Exception:
            # never let diagnostics break playback
            pass

    def _stop_play_thread(self):
        if self._play_thread and self._play_thread.is_alive():
            self._stop_event.set()
            try:
                self._play_thread.join(timeout=2.0)
            except Exception:
                pass
        self._play_thread = None
        self._stop_event.clear()
        self._active_playlist_id = None

    def _manual_slideshow_loop(
        self,
        playlist_id: int,
        items: list[dict],
        start_index: int = 0,
        *,
        first_item_preloaded: bool = False,
    ):
        """
        Manual playback loop that enforces per-item durations for images and plays videos to EOF.
        Runs in a background thread; advances images by sleeping for their duration and videos by
        polling mpv properties until EOF.
        """
        self.logger.info("Starting manual playback loop", extra={"playlist_id": playlist_id, "items_count": len(items)})

        if not items:
            return

        start_index = int(start_index or 0)
        if start_index < 0 or start_index >= len(items):
            start_index = 0

        default_duration = 10
        while not self._stop_event.is_set() and self._active_playlist_id == playlist_id:
            # Iterate cyclically starting from start_index.
            for offset in range(len(items)):
                item = items[(start_index + offset) % len(items)]
                if self._stop_event.is_set() or self._active_playlist_id != playlist_id:
                    break

                path = item["path"]
                is_video = item["is_video"]
                media_key = str(item.get("key") or path)
                raw_duration = item.get("duration")
                # Only images use duration. Treat 0/None as "missing" for images.
                duration = raw_duration if (raw_duration is not None and int(raw_duration) >= 1) else default_duration
                muted = bool(item.get("muted", False))

                # Apply per-item settings (best-effort, longer timeout to avoid IPC churn on Pi 3B+).
                # NOTE: if MPV is under load, short timeouts cause broken pipes and retry storms.
                try:
                    self._mpv_manager._send_command(
                        # Do NOT loop files. We control the loop at application level,
                        # and looping still images can cause visible "blink" on some builds.
                        {"command": ["set_property", "loop-file", "no"]},
                        timeout=5.0,
                    )
                except Exception:
                    pass

                try:
                    self._mpv_manager._send_command(
                        {"command": ["set_property", "mute", "yes" if muted else "no"]},
                        timeout=5.0,
                    )
                except Exception:
                    pass

                # Load next media file
                load_started = time.monotonic()
                # Respect per-media backoff to avoid tight loops on failing streams.
                try:
                    b = self._media_backoff.get(media_key) or {}
                    next_try = float(b.get("next_try_monotonic") or 0.0)
                except Exception:
                    next_try = 0.0
                if next_try and time.monotonic() < next_try:
                    wait_sec = max(0.0, next_try - time.monotonic())
                    self.logger.debug(
                        "Backoff active for media",
                        extra={"media_key": media_key, "wait_sec": round(wait_sec, 2)},
                    )
                    self._stop_event.wait(timeout=min(30.0, wait_sec))
                    if self._stop_event.is_set() or self._active_playlist_id != playlist_id:
                        break
                skip_load = bool(
                    first_item_preloaded and start_index == 0 and offset == 0
                )
                if not skip_load:
                    # External streams: Referer/UA must be set before loadfile (and cleared between items).
                    normalized_headers = self._apply_mpv_http_headers(item, stream_url=str(path))
                    self._apply_mpv_ytdl_options(item, stream_url=str(path))
                    load_resp = self._mpv_manager._send_command(
                        {"command": ["loadfile", path, "replace"]}, timeout=20.0
                    )
                    self._mpv_manager._send_command({"command": ["set_property", "pause", "no"]}, timeout=10.0)
                    if not load_resp or load_resp.get("error") != "success":
                        self.logger.warning(
                            "MPV loadfile failed",
                            extra={"path": path, "mpv_response": load_resp},
                        )
                        # Skip to next item; avoid getting stuck on a bad file.
                        continue

                if is_video:
                    # Network streams: mpv stays idle-active until open; do not treat idle as EOF.
                    is_network = isinstance(path, str) and (
                        path.startswith("http://")
                        or path.startswith("https://")
                        or path.startswith("ytdl://")
                    )
                    stream_ready = False
                    if is_network:
                        # For ytdl:// sources, ytdl_hook can overwrite lavf options with cookies.
                        # Re-apply/merge UA+Referer+headers after ytdl_hook writes cookies, before ffmpeg opens EDL parts.
                        if isinstance(path, str) and path.startswith("ytdl://"):
                            try:
                                self._apply_mpv_lavf_headers_after_ytdl_hook(
                                    normalized_headers=normalized_headers or {},
                                    stream_url=str(path),
                                    provider=str(item.get("provider") or "") if item.get("provider") else None,
                                    timeout_sec=8.0,
                                )
                            except Exception:
                                pass
                        if not self._wait_mpv_leave_idle(timeout_sec=60.0):
                            self.logger.warning(
                                "MPV stayed idle after loadfile (stream failed or blocked)",
                                extra={"media_key": media_key, "path_preview": str(path)[:120]},
                            )
                            self._register_media_failure(media_key, reason="stayed_idle")
                            continue
                        stream_ready = self._wait_mpv_stream_ready(str(path), timeout_sec=90.0)
                        if not stream_ready:
                            self.logger.warning(
                                "MPV stream did not become ready (no time-pos/duration/path match)",
                                extra={"media_key": media_key, "path_preview": str(path)[:120]},
                            )
                            self._register_media_failure(media_key, reason="not_ready")
                            continue
                        # Detect "instant EOF" right after we considered it ready; avoid VK loops.
                        if self._detect_mpv_instant_eof(window_sec=2.5):
                            self.logger.warning(
                                "MPV stream ended immediately after start (instant EOF)",
                                extra={"media_key": media_key, "path_preview": str(path)[:160]},
                            )
                            self._log_mpv_network_debug_snapshot(media_key=media_key, url=str(path))
                            self._register_media_failure(media_key, reason="instant_eof")
                            continue
                        self._reset_media_backoff(media_key)
                    self._wait_mpv_video_end(
                        playlist_id,
                        is_network=is_network,
                        stream_ready=stream_ready,
                        poll_sec=1.0,
                    )
                else:
                    # For still images, keep the frame open to avoid quick close/reopen churn.
                    # (Videos should not be kept open; they are EOF-driven here.)
                    try:
                        self._mpv_manager._send_command(
                            {"command": ["set_property", "keep-open", "yes"]},
                            timeout=2.0,
                        )
                    except Exception:
                        pass

                    # Deterministic image timer:
                    # wait until MPV loaded the file *and* VO is configured, then start countdown.
                    loaded = self._wait_for_mpv_loaded_path(path, timeout=15.0)
                    vo_ready = self._wait_for_mpv_vo_configured(timeout=5.0)
                    timer_start = time.monotonic()
                    load_wait_sec = round(timer_start - load_started, 3)

                    dur_sec = max(1, int(duration))
                    switch_at = timer_start + dur_sec
                    self.logger.debug(
                        "Image timer scheduled",
                        extra={
                            "path": path,
                            "duration_sec": dur_sec,
                            "loaded_confirmed": loaded,
                            "vo_configured": vo_ready,
                            "load_wait_sec": load_wait_sec,
                        },
                    )
                    self._sleep_until(switch_at, step=0.2)

    def _register_media_failure(self, media_key: str, reason: str = "unknown") -> None:
        """Exponential backoff for media that fails to start/open."""
        if not media_key:
            return
        entry = self._media_backoff.get(media_key) or {"failures": 0, "next_try_monotonic": 0.0}
        try:
            failures = int(entry.get("failures") or 0) + 1
        except Exception:
            failures = 1
        # 2s, 4s, 8s... cap at 120s, add small jitter.
        delay = min(120.0, float(2 ** min(failures, 6)))
        jitter = min(1.5, 0.1 * delay)
        next_try = time.monotonic() + delay + (jitter * (0.5))
        entry["failures"] = failures
        entry["next_try_monotonic"] = next_try
        self._media_backoff[media_key] = entry
        self.logger.warning(
            "Media backoff scheduled",
            extra={
                "media_key": media_key,
                "reason": reason,
                "failures": failures,
                "delay_sec": round(delay, 2),
            },
        )

    def _reset_media_backoff(self, media_key: str) -> None:
        if not media_key:
            return
        if media_key in self._media_backoff:
            try:
                del self._media_backoff[media_key]
            except Exception:
                self._media_backoff.pop(media_key, None)

    def play(self, playlist_id: int) -> bool:
        """Play playlist with profile support"""
        from ..models import PlaybackStatus, Playlist, PlaylistProfileAssignment, PlaybackProfile
    
        try:
            # Stop any previous manual playback loop
            self._stop_play_thread()

            # Get playlist and validate
            playlist = self.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            # Get assigned profile if exists
            profile_settings = {}
            assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=playlist_id
            ).first()
        
            if assignment and assignment.profile_id:
                profile = self.db_session.query(PlaybackProfile).get(assignment.profile_id)
                if profile:
                    # `settings` is stored as JSON in DB, so it is already a dict.
                    profile_settings = profile.settings or {}

            # Apply profile settings first
            if profile_settings:
                if not self._mpv_manager.update_settings(profile_settings):
                    self.logger.warning("Failed to apply some profile settings")

            # panscan>0 даёт «зум под размер экрана» и на части DRM/сборок ведёт к обрезке даже при 16:9.
            # По умолчанию вписываем кадр без обрезки; при необходимости убрать полосы — задать panscan в профиле MPV.
            if "panscan" not in profile_settings:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "panscan", 0.0]},
                    timeout=2.0,
                )

            # Manual playback loop is the most reliable way to enforce per-item durations on mpv builds
            # where ffconcat timing is inconsistent for images and mixed media.
            items = []
            missing = []
            # Enforce stable playback order (PlaylistFiles.order in DB).
            files = sorted((playlist.files or []), key=lambda x: int(getattr(x, "order", 0) or 0))
            for pf in files:
                resolved = self._resolve_playlist_item_path(getattr(pf, "file_name", None))
                if not resolved or not resolved.get("path"):
                    missing.append(str(getattr(pf, "file_name", "")))
                    continue

                is_video = bool(resolved.get("is_video"))
                items.append(
                    {
                        "key": resolved.get("key"),
                        "path": resolved["path"],
                        "duration": int(getattr(pf, "duration", 0) or 0),
                        "is_video": is_video,
                        "muted": bool(getattr(pf, "muted", False)) if is_video else False,
                        "http_headers": resolved.get("http_headers") or {},
                        "page_url": resolved.get("page_url"),
                        "provider": resolved.get("provider"),
                    }
                )

            if not items:
                raise ValueError(
                    f"Playlist {playlist_id} has no existing media files"
                    + (f". Missing: {', '.join(missing[:10])}" if missing else "")
                    + (" ..." if len(missing) > 10 else "")
                )

            self._active_playlist_id = playlist_id

            # Show first item immediately for responsiveness
            first = items[0]
            try:
                # Do NOT loop the file at MPV level; the app controls looping.
                self._mpv_manager._send_command(
                    {"command": ["set_property", "loop-file", "no"]},
                    timeout=2.0,
                )
            except Exception:
                pass
            try:
                self._mpv_manager._send_command(
                    {"command": ["set_property", "mute", "yes" if bool(first.get("muted")) else "no"]},
                    timeout=2.0,
                )
            except Exception:
                pass
            self._apply_mpv_http_headers(first, stream_url=str(first.get("path") or ""))
            self._mpv_manager._send_command({"command": ["loadfile", first["path"], "replace"]}, timeout=10.0)
            self._mpv_manager._send_command({"command": ["set_property", "pause", "no"]}, timeout=5.0)

            # Update playback status (single-row table; keep id=1 stable)
            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            playback.playlist_id = playlist_id
            playback.status = 'playing'
            self.db_session.add(playback)
            self.db_session.commit()

            # Start background loop to enforce durations and EOF waits.
            # Multi-item: start from index 1 because we already loadfile'd items[0] above.
            # Single-item: start at 0 and skip the redundant loadfile in the loop (same URL/headers).
            loop_start = 1 if len(items) > 1 else 0
            self._play_thread = Thread(
                target=self._manual_slideshow_loop,
                args=(playlist_id, items, loop_start),
                kwargs={"first_item_preloaded": len(items) == 1},
                daemon=True,
            )
            self._play_thread.start()

            # Notify clients
            try:
                if self.socketio:
                    self.socketio.emit(
                        'playback_update',
                        {
                            'status': 'playing',
                            'playlist_id': playlist.id,
                            'playlist': {'id': playlist.id, 'name': playlist.name},
                            'settings': profile_settings,
                        },
                    )
            except Exception:
                # Best-effort: playback must continue even if sockets are unavailable.
                pass
        
            return True

        except Exception as e:
            self.logger.error(
                "Playback error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
            try:
                self.db_session.rollback()
            except Exception:
                pass

            # Best-effort: persist non-playing state so UI doesn't show green when we fell back to idle
            try:
                from ..models import PlaybackStatus
                playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
                playback.status = 'idle'
                playback.playlist_id = None
                self.db_session.add(playback)
                self.db_session.commit()
            except Exception:
                try:
                    self.db_session.rollback()
                except Exception:
                    pass

            # Fall back to idle logo
            self._logo_manager.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")

    def stop(self) -> bool:
        """Stop playback and persist stopped state so UI/API match MPV (idle logo)."""
        from ..models import PlaybackStatus

        try:
            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            last_playlist_id = playback.playlist_id

            self._stop_play_thread()
            ok = self._logo_manager.display_idle_logo()

            playback = self.db_session.query(PlaybackStatus).get(1) or PlaybackStatus(id=1)
            playback.status = "stopped"
            playback.playlist_id = last_playlist_id
            self.db_session.add(playback)
            self.db_session.commit()

            try:
                if self.socketio:
                    self.socketio.emit(
                        'playback_update',
                        {
                            'status': 'stopped',
                            'playlist_id': last_playlist_id,
                        },
                    )
            except Exception:
                pass
            return ok
        except Exception as e:
            self.logger.error(
                "Stop error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
            return False

    def get_status(self) -> Dict:
        """Get current playback status"""
        from ..models import PlaybackStatus
        
        status = self.db_session.query(PlaybackStatus).get(1) or self.db_session.query(PlaybackStatus).first()
        return {
            'status': status.status if status else None,
            'playlist_id': status.playlist_id if status else None,
            'settings': self._mpv_manager._current_settings
        }

    def restart_mpv(self) -> bool:
        """Restart MPV process with enhanced reliability"""
        try:
            if self._mpv_manager._mpv_process:
                try:
                    self._mpv_manager._mpv_process.terminate()
                    self._mpv_manager._mpv_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.logger.warning("MPV process did not terminate gracefully, killing...")
                    self._mpv_manager._mpv_process.kill()
                    self._mpv_manager._mpv_process.wait()
                except Exception as e:
                    self.logger.warning(f"Error terminating MPV process: {str(e)}")
            
            if os.path.exists(PlaybackConstants.SOCKET_PATH):
                try:
                    os.unlink(PlaybackConstants.SOCKET_PATH)
                except Exception as e:
                    self.logger.warning(f"Error removing socket: {str(e)}")
                    try:
                        os.chmod(PlaybackConstants.SOCKET_PATH, 0o777)
                        os.unlink(PlaybackConstants.SOCKET_PATH)
                    except:
                        pass
            
            self._mpv_manager._mpv_ready = False
            self._mpv_manager._socket_ready_event.clear()
            self._mpv_manager._ensure_mpv_service()
            
            try:
                self._mpv_manager._wait_for_mpv_ready(timeout=30)
                return True
            except Exception as e:
                self.logger.error(f"Failed to verify MPV restart: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(
                "MPV restart failed",
                extra={
                    "error": str(e),
                    "type": type(e).__name__,
                    "stack_trace": traceback.format_exc()
                }
            )
            return False

    def get_playback_info(self) -> Dict:
        """Get current playback info"""
        info = {}
        for category, settings in self._mpv_manager._current_settings.items():
            info[category] = {}
            for setting in settings.keys():
                response = self._mpv_manager._send_command({
                    "command": ["get_property", setting]
                })
                if response and 'data' in response:
                    info[category][setting] = response['data']
        return info
        
    def stop_idle_logo(self):
        """Stop idle logo display"""
        try:
            res = self._mpv_manager._send_command({"command": ["stop"]})
            if res is not None:
                self.logger.info("Idle logo stopped")
            else:
                self.logger.warning("Failed to get confirmation of idle logo stop")
        except Exception as e:
            self.logger.error(f"Failed to stop idle logo: {str(e)}")
            
    def restart_idle_logo(self) -> bool:
        """Restart idle logo display"""
        try:
            self.stop_idle_logo()
            return self._logo_manager.display_idle_logo()
        except Exception as e:
            self.logger.error(f"Failed to restart idle logo: {str(e)}")
            return False
