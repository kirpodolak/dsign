"""Network stream helpers extracted from PlaylistManager (H-REF PR2)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .playback_constants import PlaybackConstants
from .playback_eof import float_env

if TYPE_CHECKING:
    from .playlist_management import PlaylistManager


class PlaybackNetworkHelper:
    """ytdl/http stream open, headers, midstream reload, stream-ready waits."""

    def __init__(self, pm: "PlaylistManager") -> None:
        self._pm = pm

    def _network_open_aborted(self, playback_run_id: Optional[int] = None) -> bool:
        """True when Stop/newer play invalidated this open wait."""
        if self._pm._stop_event.is_set():
            return True
        if playback_run_id is None:
            return False
        try:
            return not self._pm._is_playback_run_current(int(playback_run_id))
        except Exception:
            return True

    def _ensure_network_stream_started(
        self,
        item: Dict[str, Any],
        stream_url: str,
        *,
        normalized_headers: Optional[Dict[str, str]] = None,
        load_cmd: Optional[List[Any]] = None,
        load_ipc_ok: bool = True,
        playback_run_id: Optional[int] = None,
    ) -> bool:
        """Wait until mpv opens a network stream (background thread only)."""
        path_s = str(stream_url or "")
        media_key = str(item.get("key") or path_s)
        if not path_s.startswith(("http://", "https://", "ytdl://")):
            return True

        headers = normalized_headers
        if not headers:
            headers = self._sanitize_headers_for_mpv(
                item.get("http_headers") or {},
                page_url=item.get("page_url"),
                stream_url=path_s,
                provider=item.get("provider"),
            )

        try:
            self._pm._mpv_manager.set_playback_stream_opening(True)
        except Exception:
            pass
        try:
            return self._ensure_network_stream_started_impl(
                item,
                path_s,
                media_key,
                headers=headers,
                load_cmd=load_cmd,
                load_ipc_ok=load_ipc_ok,
                playback_run_id=playback_run_id,
            )
        finally:
            try:
                self._pm._mpv_manager.set_playback_stream_opening(False)
            except Exception:
                pass

    def _ytdl_stream_open_progress(self) -> Optional[str]:
        """True progress signal for ytdl:// resolution (not idle-active alone)."""
        soc_raw = self._pm._mpv_get_light("stream-open-filename", timeout=0.35)
        soc = self._pm._snap_str({"stream-open-filename": soc_raw}, "stream-open-filename")
        if soc and not str(soc).startswith("ytdl://") and len(str(soc).strip()) > 8:
            return "stream-open-filename"
        pth_raw = self._pm._mpv_get_light("path", timeout=0.35)
        pth = self._pm._snap_str({"path": pth_raw}, "path")
        if pth and not str(pth).startswith("ytdl://") and len(str(pth).strip()) > 8:
            return "path"
        dct_raw = self._pm._mpv_get_light("demuxer-cache-time", timeout=0.35)
        dct = self._pm._snap_number({"demuxer-cache-time": dct_raw}, "demuxer-cache-time")
        if dct is not None and dct > 0.05:
            return "demuxer-cache-time"
        dem_raw = self._pm._mpv_get_light("demuxer", timeout=0.35)
        dem = self._pm._snap_str({"demuxer": dem_raw}, "demuxer")
        if dem and str(dem).strip():
            tp_raw = self._pm._mpv_get_light("time-pos", timeout=0.35)
            if self._pm._snap_number({"time-pos": tp_raw}, "time-pos") is not None:
                return "demuxer+time-pos"
        return None

    def _wait_mpv_ytdl_stream_opening(
        self,
        *,
        timeout_sec: float = 180.0,
        playback_run_id: Optional[int] = None,
    ) -> bool:
        """Poll until ytdl_hook resolves to a real stream URL or playback starts."""
        try:
            timeout_sec = float(
                (os.getenv("DSIGN_MPV_YTDL_OPEN_SEC") or str(timeout_sec)).strip()
            )
        except ValueError:
            pass
        # Floor 5s (was 30s) so Stop/run-id abort is not padded after short waits.
        deadline = time.monotonic() + max(5.0, min(600.0, float(timeout_sec)))
        while time.monotonic() < deadline:
            if self._network_open_aborted(playback_run_id):
                return False
            reason = self._ytdl_stream_open_progress()
            if reason:
                self._pm.logger.info(
                    "ytdl stream opening progress",
                    extra={"reason": reason},
                )
                return True
            self._pm._stop_event.wait(timeout=0.75)
        return False

    def _ensure_network_stream_started_impl(
        self,
        item: Dict[str, Any],
        path_s: str,
        media_key: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        load_cmd: Optional[List[Any]] = None,
        load_ipc_ok: bool = True,
        playback_run_id: Optional[int] = None,
    ) -> bool:
        is_ytdl = path_s.startswith("ytdl://")
        if is_ytdl:
            if load_cmd is not None and not load_ipc_ok:
                if not self._wait_mpv_ytdl_stream_opening(
                    timeout_sec=20.0, playback_run_id=playback_run_id
                ):
                    if self._network_open_aborted(playback_run_id):
                        return False
                    self._pm.logger.info(
                        "ytdl: re-issuing loadfile after IPC quiet",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    self._pm._issue_ytdl_loadfile(load_cmd, media_key=media_key)
            # Cold open (streak==0 after Play reset): give VK/Rutube ~120s+60s.
            # After failures, _ytdl_open_timeout_sec caps tighter so we still fail fast.
            open_sec = self._pm._ytdl_open_timeout_sec(120.0)
            if not self._wait_mpv_ytdl_stream_opening(
                timeout_sec=open_sec, playback_run_id=playback_run_id
            ):
                if self._network_open_aborted(playback_run_id):
                    return False
                if load_cmd is not None:
                    self._pm.logger.info(
                        "ytdl: retrying loadfile after open wait timed out",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    self._pm._issue_ytdl_loadfile(load_cmd, media_key=media_key)
                    retry_sec = self._pm._ytdl_open_timeout_sec(60.0)
                    if not self._wait_mpv_ytdl_stream_opening(
                        timeout_sec=retry_sec, playback_run_id=playback_run_id
                    ):
                        if self._network_open_aborted(playback_run_id):
                            return False
                        self._pm.logger.warning(
                            "ytdl stream did not open after loadfile retries",
                            extra={"media_key": media_key, "path_preview": path_s[:120]},
                        )
                        return False
                else:
                    self._pm.logger.warning(
                        "ytdl stream did not open",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    return False
            try:
                self._apply_mpv_lavf_headers_after_ytdl_hook(
                    normalized_headers=headers or {},
                    stream_url=path_s,
                    provider=str(item.get("provider") or "") if item.get("provider") else None,
                    timeout_sec=12.0,
                    skip_idle_wait=True,
                )
            except Exception:
                pass
            if self._network_open_aborted(playback_run_id):
                return False
            if self._pm._mpv_get_light("time-pos", timeout=2.0) is not None:
                pass
            elif not self._wait_mpv_network_demuxer_ready(timeout_sec=30.0, poll_sec=0.5):
                if self._network_open_aborted(playback_run_id):
                    return False
                if self._ytdl_stream_open_progress() is None:
                    self._pm.logger.warning(
                        "ytdl demuxer not ready after stream URL resolved",
                        extra={"media_key": media_key, "path_preview": path_s[:120]},
                    )
                    self._log_mpv_network_debug_snapshot(media_key=media_key, url=path_s)
                    return False
        else:
            leave_to = 90.0
            if not self._wait_mpv_leave_idle(
                timeout_sec=leave_to, poll_sec=0.6, snap_timeout=8.0
            ):
                if self._network_open_aborted(playback_run_id):
                    return False
                self._pm.logger.warning(
                    "Network stream did not leave idle after play() loadfile",
                    extra={"media_key": media_key, "path_preview": path_s[:120]},
                )
                return False

            try:
                dem_to = float((os.getenv("DSIGN_MPV_DEMUXER_WAIT_SEC") or "45").strip())
            except ValueError:
                dem_to = 45.0
            dem_to = max(5.0, min(120.0, dem_to))
            if not self._wait_mpv_network_demuxer_ready(timeout_sec=dem_to, poll_sec=0.4):
                if self._network_open_aborted(playback_run_id):
                    return False
                self._pm.logger.warning(
                    "Network stream demuxer not ready after play() loadfile",
                    extra={"media_key": media_key, "path_preview": path_s[:120]},
                )
                self._log_mpv_network_debug_snapshot(media_key=media_key, url=path_s)
                return False

        if self._detect_mpv_instant_eof(window_sec=2.5):
            self._pm.logger.warning(
                "Network stream instant EOF after play() loadfile",
                extra={"media_key": media_key, "path_preview": path_s[:120]},
            )
            return False

        self._pm.logger.info(
            "Network stream ready after play() loadfile",
            extra={"media_key": media_key, "path_preview": path_s[:120]},
        )
        self._pm._reset_media_backoff(media_key)
        try:
            self._pm._mpv_manager._reset_playback_ipc_fail_streak()
        except Exception:
            pass
        return True

    def _try_midstream_network_reload(
        self,
        item: Dict[str, Any],
        *,
        playlist_id: int,
        reason: str,
        seek_to: Optional[float] = None,
    ) -> bool:
        """
        Reload ytdl/http stream via loadfile without systemd-restarting mpv.
        Used when time-pos freezes mid-roll but IPC still responds.

        When ``seek_to`` is set (proactive refresh), re-resolve URL and resume near
        the saved position instead of restarting from 0:00.
        """
        media_key = str(item.get("key") or item.get("path") or "")
        saved_seek = seek_to
        if saved_seek is None:
            saved_seek = self._pm._snap_number(
                {"time-pos": self._pm._mpv_get_light("time-pos", timeout=3.0)},
                "time-pos",
            )
        if not self._pm._refresh_item_playback_path(item):
            self._pm.logger.warning(
                "playlist: mid-stream reload skipped (URL refresh failed)",
                extra={
                    "playlist_id": playlist_id,
                    "media_key": media_key,
                    "reason": reason,
                },
            )
            return False
        path = str(item.get("path") or "")
        if not path.startswith(("http://", "https://", "ytdl://")):
            return False
        self._pm.logger.info(
            "playlist: mid-stream network reload",
            extra={
                "playlist_id": playlist_id,
                "media_key": media_key,
                "reason": reason,
                "seek_to": saved_seek,
                "path_preview": path[:120],
            },
        )
        try:
            self._pm._mpv_manager.set_playback_stream_opening(True)
        except Exception:
            pass
        try:
            normalized_headers, mpv_per_file_opts = self._apply_mpv_http_headers(
                item, stream_url=path
            )
            self._apply_mpv_ytdl_options(item, stream_url=path)
            load_cmd = self._pm._mpv_loadfile_command(
                path, "replace", per_file_opts=mpv_per_file_opts
            )
            if path.startswith("ytdl://"):
                self._pm._issue_ytdl_loadfile(load_cmd, media_key=media_key)
            else:
                self._pm._issue_loadfile(
                    load_cmd,
                    media_key=media_key,
                    force=True,
                    timeout=self._pm._network_loadfile_timeout_sec(path, is_network=True),
                )
            if not self._ensure_network_stream_started(
                item,
                path,
                normalized_headers=normalized_headers,
                load_cmd=load_cmd,
                load_ipc_ok=True,
            ):
                return False
            try:
                self._pm._mpv_manager.set_playback_stream_opening(True)
            except Exception:
                pass
            if saved_seek is not None and saved_seek > 5.0:
                try:
                    self._pm._mpv_manager._send_command(
                        {"command": ["seek", float(saved_seek), "absolute"]},
                        timeout=8.0,
                    )
                    self._pm._mpv_manager._send_command(
                        {"command": ["set_property", "pause", "no"]},
                        timeout=3.0,
                    )
                except Exception:
                    pass
            self._pm._stop_event.wait(timeout=8.0)
            tp_after = self._pm._snap_number(
                {"time-pos": self._pm._mpv_get_light("time-pos", timeout=3.0)},
                "time-pos",
            )
            if saved_seek is not None and saved_seek > 5.0:
                ok = (
                    tp_after is not None
                    and tp_after >= max(0.0, float(saved_seek) - 45.0)
                )
            else:
                ok = tp_after is not None and tp_after > 0.2
            if not ok:
                self._pm.logger.warning(
                    "playlist: mid-stream reload did not resume playback",
                    extra={
                        "playlist_id": playlist_id,
                        "media_key": media_key,
                        "seek_to": saved_seek,
                        "time_pos_after": tp_after,
                    },
                )
            return ok
        except Exception as e:
            self._pm.logger.warning(
                "playlist: mid-stream reload failed",
                extra={
                    "playlist_id": playlist_id,
                    "media_key": media_key,
                    "error": str(e),
                    "type": type(e).__name__,
                },
            )
            return False
        finally:
            try:
                self._pm._mpv_manager.set_playback_stream_opening(False)
            except Exception:
                pass

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
        svc = self._pm._external_media_service
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

    @staticmethod
    def _escape_mpv_key_value_list_token(s: str) -> str:
        """Escape a token for mpv's comma-separated key=value list options (e.g. --stream-lavf-o)."""
        return (
            str(s)
            .replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace("=", "\\=")
        )

    def _format_mpv_key_value_list(self, d: Dict[str, str]) -> str:
        parts: list[str] = []
        for k, v in (d or {}).items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v)
            if not ks or not vs:
                continue
            parts.append(
                f"{self._escape_mpv_key_value_list_token(ks)}={self._escape_mpv_key_value_list_token(vs)}"
            )
        return ",".join(parts)

    def _clear_mpv_http_options(self, *, fast: bool = False) -> None:
        """Reset per-stream HTTP options so a previous item cannot poison the next load."""
        timeout = 0.35 if fast else 3.0
        attempts = 1 if fast else None
        cmds = [
            ["set_property", "http-header-fields", ""],
            ["set_property", "user-agent", ""],
            ["set_property", "referrer", ""],
            ["set_property", "ytdl-format", ""],
        ]
        for cmd in cmds:
            try:
                self._pm._mpv_manager._send_command(
                    {"command": cmd},
                    timeout=timeout,
                    max_attempts=attempts,
                )
            except Exception:
                pass

    def _build_mpv_stream_lavf_o_opts(
        self,
        headers: Dict[str, str],
        *,
        stream_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Build libavformat options for network opens (used as per-file `stream-lavf-o` on loadfile).

        When mpv plays `edl://` sources from ytdl_hook, the underlying open can go through
        lavf/ffmpeg. Those opens may ignore `http-header-fields`, so we pass `stream-lavf-o`
        as a per-file option (mpv `loadfile` option list), plus `user-agent`/`referrer` fallbacks.

        IMPORTANT: Avoid duplicating UA/Referer between multiple mechanisms when possible.
        """
        if not headers:
            return {}
        ua = str(headers.get("User-Agent") or "").strip()
        ref = str(headers.get("Referer") or "").strip()
        cookie = str(headers.get("Cookie") or "").strip()
        if not ua and not ref and not cookie:
            return {}

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
            if lk == "user-agent" and ua:
                # `user_agent` is already provided via the dedicated option; avoid duplicating it.
                continue
            if is_rutube_cdn_hdr and lk in ("referer", "referrer", "origin"):
                # `referer` is already provided via the dedicated option; Origin is not required for ffmpeg.
                continue
            if lk == "accept-language":
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

        if stream_url and str(stream_url).startswith(
            ("http://", "https://", "ytdl://")
        ):
            for k, v in self._lavf_network_timeout_opts().items():
                opts.setdefault(k, v)

        # mpv-level fallbacks; they can help non-lavf opens too.
        if ua:
            try:
                self._pm._mpv_manager._send_command(
                    {"command": ["set_property", "user-agent", ua]},
                    timeout=3.0,
                )
            except Exception:
                pass
        if ref:
            try:
                self._pm._mpv_manager._send_command(
                    {"command": ["set_property", "referrer", ref]},
                    timeout=3.0,
                )
            except Exception:
                pass
        return opts

    @staticmethod
    def _is_valid_lavf_option_key(key: str) -> bool:
        """Reject ytdl_hook/parser debris (e.g. en;q=0.9 split into en;q\\ AVOption keys)."""
        ks = str(key or "").strip()
        if not ks:
            return False
        if ";" in ks or "\\" in ks or "=" in ks:
            return False
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*$", ks))

    @staticmethod
    def _strip_accept_language_header_lines(blob: str) -> str:
        lines = []
        for line in str(blob or "").replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.lower().startswith("accept-language:"):
                continue
            lines.append(line.rstrip("\r"))
        return "\r\n".join(lines)

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
                if not self._is_valid_lavf_option_key(ks):
                    continue
                if ks == "headers":
                    vs = self._strip_accept_language_header_lines(vs)
                    if not vs.strip():
                        continue
                out[ks] = vs
        for k, v in (extra or {}).items():
            if k is None or v is None:
                continue
            ks = str(k).strip()
            vs = str(v).strip()
            if not ks or not vs:
                continue
            if not self._is_valid_lavf_option_key(ks):
                continue
            if ks == "headers":
                vs = self._strip_accept_language_header_lines(vs)
                if not vs.strip():
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
        skip_idle_wait: bool = False,
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
            if lk in ("user-agent", "referer", "referrer", "accept-language"):
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

        # `file-local-options/*` is only meaningful while a file is being opened/played.
        if not skip_idle_wait:
            if not self._wait_mpv_leave_idle(timeout_sec=min(8.0, float(timeout_sec))):
                return

        # Wait briefly for ytdl_hook to populate cookies, then merge+set.
        deadline = time.monotonic() + max(0.2, float(timeout_sec))
        base_val: Any = None
        while time.monotonic() < deadline and not self._pm._stop_event.is_set():
            try:
                resp = self._pm._mpv_manager._send_command(
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
            self._pm._stop_event.wait(timeout=0.15)

        merged = self._merge_mpv_lavf_options(base_val, extra)
        try:
            # Log with a string message + extras; some logging pipelines drop dict-as-message.
            self._pm.logger.info(
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
            self._pm._mpv_manager._send_command(
                {"command": ["set_property", "file-local-options/stream-lavf-o", merged]},
                timeout=5.0,
            )
        except Exception:
            pass
        # Also keep mpv fallbacks in sync.
        if ua:
            try:
                self._pm._mpv_manager._send_command(
                    {"command": ["set_property", "user-agent", ua]},
                    timeout=3.0,
                )
            except Exception:
                pass
        if ref:
            try:
                self._pm._mpv_manager._send_command(
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

        Prefer muxed streams capped at 1080p for VK. Uncapped `best` often resolves to 4K H.264,
        which software-decodes on signage hardware and runs much hotter than typical Rutube HLS.
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
                self._pm._mpv_manager._send_command(
                    {
                        "command": [
                            "set_property",
                            "ytdl-format",
                            PlaybackConstants.VK_YTDL_FORMAT,
                        ]
                    },
                    timeout=2.0,
                    max_attempts=1,
                )
            except Exception:
                pass

    def _apply_mpv_http_headers(self, item: dict, *, stream_url: str) -> tuple[Dict[str, str], Dict[str, Any]]:
        """
        Set MPV HTTP options for one playlist item. Always clears stale options first.

        Returns:
        - normalized headers dict (may be empty)
        - per-file mpv options for the next `loadfile` command (mpv 0.38+: use index -1 when passing options)
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
            return {}, {}

        normalized = self._sanitize_headers_for_mpv(
            http_headers,
            page_url=page_url,
            stream_url=stream_url,
            provider=provider,
        )
        is_ytdl_url = isinstance(stream_url, str) and stream_url.startswith("ytdl://")
        # ytdl:// opens carry Referer/UA via per-file loadfile opts; clearing globals while
        # mpv is busy (ytdl_hook) blocks IPC for tens of seconds with no benefit.
        if not is_ytdl_url:
            self._clear_mpv_http_options(fast=bool(self._pm._mpv_manager._playback_session_active))

        per_file_opts: Dict[str, Any] = {}
        per_file_opts.update(self._pm._collect_mpv_network_buffering_per_file(item, stream_url=stream_url))

        # For network streams opened via lavf/ffmpeg (incl. direct Rutube river/rtbcdn URLs),
        # pass `stream-lavf-o` as a per-file option on `loadfile` (do not use `set_property` while idle).
        is_network = isinstance(stream_url, str) and stream_url.startswith(("http://", "https://", "ytdl://"))
        if is_network and normalized:
            lavf = self._build_mpv_stream_lavf_o_opts(
                normalized,
                stream_url=stream_url,
                provider=provider,
            )
            lavf_blob = self._format_mpv_key_value_list(lavf)
            if lavf_blob:
                per_file_opts["stream-lavf-o"] = lavf_blob

        # Rutube direct CDN (river-*/rtbcdn): the actual open goes through lavf/ffmpeg.
        # Setting global `http-header-fields` in parallel (Cookie/Referer/Origin/UA) can
        # duplicate or fight lavf request context and some CDNs return 400.
        # Leave `http-header-fields` empty after _clear_mpv_http_options; rely on per-file `stream-lavf-o` + mpv user-agent/referrer.
        skip_global_http_header_fields = False
        try:
            prov_h = (str(provider or "").strip().lower()) if provider is not None else ""
            su_h = (str(stream_url or "").strip().lower()) if stream_url is not None else ""
            if prov_h == "rutube" and is_network and not str(stream_url or "").startswith(
                "ytdl://"
            ):
                if "river-" in su_h or ".rtbcdn.ru/" in su_h or "rtbcdn.ru/" in su_h:
                    skip_global_http_header_fields = True
        except Exception:
            pass

        if not skip_global_http_header_fields:
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
                    self._pm._mpv_manager._send_command(
                        {
                            "command": ["set_property", "http-header-fields", "\r\n".join(header_lines)],
                        },
                        timeout=2.0,
                        max_attempts=1,
                    )
                # For VK/OKCDN we prefer to keep UA/Referer here too: ytdl_hook may overwrite
                # lavf options later (cookies-only), but it won't clobber http-header-fields.
            except Exception:
                pass
        return normalized, per_file_opts

    def _wait_mpv_leave_idle(
        self,
        timeout_sec: float = 45.0,
        *,
        poll_sec: float = 0.5,
        snap_timeout: float = 8.0,
    ) -> bool:
        """
        After loadfile, mpv often reports idle-active=true until the demuxer opens.
        Our old loop treated idle as EOF and skipped network streams in ~1 tick.
        """
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        poll_sec = max(0.25, float(poll_sec))
        snap_timeout = max(3.0, min(20.0, float(snap_timeout)))
        while time.monotonic() < deadline:
            if self._pm._stop_event.is_set():
                return False
            idle_raw = self._pm._mpv_get_light("idle-active", timeout=snap_timeout)
            if isinstance(idle_raw, bool) and idle_raw is False:
                return True
            self._pm._stop_event.wait(timeout=poll_sec)
        return False

    def _wait_mpv_network_demuxer_ready(self, *, timeout_sec: float = 45.0, poll_sec: float = 0.25) -> bool:
        """
        After leave-idle, mpv can briefly report idle-active=false while HLS demuxer has not opened yet.
        Treat as failure if we return to idle before demuxer/path indicates an active open — avoids false
        instant_eof when loadfile fails quickly (403, TLS, CDN).
        """
        deadline = time.monotonic() + max(2.0, float(timeout_sec))
        poll_tick = 0
        while time.monotonic() < deadline:
            if self._pm._stop_event.is_set():
                return False
            poll_tick += 1
            idle_raw = self._pm._mpv_get_light("idle-active", timeout=0.35)
            if isinstance(idle_raw, bool) and idle_raw is True:
                return False
            if poll_tick % 3 == 0:
                eof_raw = self._pm._mpv_get_light("eof-reached", timeout=0.35)
                if self._pm._snap_bool({"eof-reached": eof_raw}, "eof-reached") is True:
                    return False

            dem_raw = self._pm._mpv_get_light("demuxer", timeout=0.35)
            dem = self._pm._snap_str({"demuxer": dem_raw}, "demuxer")
            if dem and str(dem).strip():
                return True
            if poll_tick % 2 == 0:
                soc_raw = self._pm._mpv_get_light("stream-open-filename", timeout=0.35)
                soc = self._pm._snap_str({"stream-open-filename": soc_raw}, "stream-open-filename")
                if soc and len(str(soc).strip()) > 8:
                    return True
                pth_raw = self._pm._mpv_get_light("path", timeout=0.35)
                pth = self._pm._snap_str({"path": pth_raw}, "path")
                if pth and len(str(pth).strip()) > 8 and not str(pth).startswith("ytdl://"):
                    return True
                dct_raw = self._pm._mpv_get_light("demuxer-cache-time", timeout=0.35)
                dct = self._pm._snap_number({"demuxer-cache-time": dct_raw}, "demuxer-cache-time")
                if dct is not None and dct > 0.02:
                    return True

            self._pm._stop_event.wait(timeout=max(0.1, float(poll_sec)))
        return False

    def _wait_mpv_stream_ready(
        self,
        expected_url: str,
        timeout_sec: Optional[float] = None,
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
        def _float_env(name: str, default: float, *, lo: float, hi: float) -> float:
            try:
                v = float((os.getenv(name) or "").strip())
            except ValueError:
                return default
            return max(lo, min(hi, v))

        if timeout_sec is None:
            timeout_sec = _float_env("DSIGN_MPV_STREAM_READY_SECS", 120.0, lo=15.0, hi=600.0)
        grace_sec = _float_env("DSIGN_MPV_STREAM_READY_GRACE_SEC", 45.0, lo=5.0, hi=300.0)

        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        grace_until = time.monotonic() + grace_sec
        exp = (expected_url or "").strip()
        is_ytdl = exp.startswith("ytdl://")
        # For ytdl:// resolution on low-power devices, be gentle with IPC:
        # - ytdl_hook may take >10s
        # - mpv may update stream-open-filename earlier than time-pos/duration
        heavy_every = 2 if is_ytdl else 1
        tick = 0

        while time.monotonic() < deadline:
            if self._pm._stop_event.is_set():
                return False

            snap_loop: Dict[str, Any] = {}
            if is_ytdl:
                snap_loop = self._pm._mpv_snapshot(
                    [
                        "stream-open-filename",
                        "path",
                        "demuxer-cache-time",
                        "demuxer-cache-duration",
                        "demuxer",
                        "time-pos",
                        "duration",
                    ],
                    timeout=2.0,
                )
                cur_stream = self._pm._snap_str(snap_loop, "stream-open-filename")
                if cur_stream and not str(cur_stream).startswith("ytdl://"):
                    return True
                cur_path = self._pm._snap_str(snap_loop, "path")
                if cur_path and not str(cur_path).startswith("ytdl://"):
                    return True
                dct = self._pm._snap_number(snap_loop, "demuxer-cache-time")
                if dct is not None and dct > 0.05:
                    return True
                dcdur = self._pm._snap_number(snap_loop, "demuxer-cache-duration")
                if dcdur is not None and dcdur > 0.05:
                    return True
                dem = self._pm._snap_str(snap_loop, "demuxer")
                if dem and str(dem).strip():
                    tp_y = self._pm._snap_number(snap_loop, "time-pos")
                    if tp_y is not None:
                        return True
                    dur_y = self._pm._snap_number(snap_loop, "duration")
                    if dur_y is not None and dur_y > 0:
                        return True
            else:
                snap_loop = self._pm._mpv_snapshot(
                    [
                        "stream-open-filename",
                        "path",
                        "demuxer-cache-time",
                        "demuxer-cache-duration",
                        "idle-active",
                        "eof-reached",
                        "paused-for-cache",
                        "core-idle",
                        "time-pos",
                        "duration",
                    ],
                    timeout=2.0,
                )
                cur_stream = self._pm._snap_str(snap_loop, "stream-open-filename")
                cur_path = self._pm._snap_str(snap_loop, "path")
                if (cur_stream and len(str(cur_stream)) > 0) or (
                    cur_path and len(str(cur_path)) > 0
                ):
                    # do not early-return; keep probing time-pos/duration below
                    pass
                dct = self._pm._snap_number(snap_loop, "demuxer-cache-time")
                if dct is not None and dct > 0.05:
                    return True
                dcdur = self._pm._snap_number(snap_loop, "demuxer-cache-duration")
                if dcdur is not None and dcdur > 0.05:
                    return True

                idle = self._pm._snap_bool(snap_loop, "idle-active")
                eof = self._pm._snap_bool(snap_loop, "eof-reached")
                paused_cache = self._pm._snap_bool(snap_loop, "paused-for-cache")
                core_idle = self._pm._snap_bool(snap_loop, "core-idle")

                if idle is False and eof is not True:
                    if paused_cache is True:
                        pass
                    elif paused_cache is False:
                        return True
                    else:
                        if core_idle is False:
                            return True
                        if time.monotonic() >= grace_until:
                            return True
            tick += 1
            if heavy_every > 1 and (tick % heavy_every) != 0:
                self._pm._stop_event.wait(timeout=max(0.15, float(poll_sec)))
                continue
            tp = self._pm._snap_number(snap_loop, "time-pos")
            if tp is not None:
                return True
            dur = self._pm._snap_number(snap_loop, "duration")
            if dur is not None and dur > 0:
                return True
            self._pm._stop_event.wait(timeout=max(0.1, float(poll_sec)))
        return False

    def _detect_mpv_instant_eof(self, *, window_sec: float = 2.5) -> bool:
        """
        Detect a pathological case where MPV opens a file/stream and immediately returns to idle.
        This happens with some VK `edl://` selections (separate streams / blocked URL) and should
        be treated as a start failure (so we backoff instead of busy-looping).

        Require evidence the demuxer actually opened — otherwise idle flicker before HLS attaches
        looks like instant EOF on Rutube/CDN.
        """
        deadline = time.monotonic() + max(0.2, float(window_sec))
        saw_not_idle = False
        saw_demuxer = False
        while time.monotonic() < deadline:
            if self._pm._stop_event.is_set():
                return False
            idle_raw = self._pm._mpv_get_light("idle-active", timeout=0.35)
            idle = self._pm._snap_bool({"idle-active": idle_raw}, "idle-active")
            if idle is False:
                saw_not_idle = True
            dem_raw = self._pm._mpv_get_light("demuxer", timeout=0.35)
            dem = self._pm._snap_str({"demuxer": dem_raw}, "demuxer")
            if dem and str(dem).strip():
                saw_demuxer = True
            else:
                dct_raw = self._pm._mpv_get_light("demuxer-cache-time", timeout=0.35)
                if self._pm._snap_number({"demuxer-cache-time": dct_raw}, "demuxer-cache-time") not in (
                    None,
                    0.0,
                ):
                    saw_demuxer = True

            if saw_not_idle and idle is True and saw_demuxer:
                return True
            self._pm._stop_event.wait(timeout=0.1)
        return False

    def _lavf_network_timeout_opts(self) -> Dict[str, str]:
        """ffmpeg/lavf read timeouts (microseconds) — avoid indefinite demuxer blocks."""
        sec = float_env("DSIGN_MPV_LAVF_TIMEOUT_SEC", 15.0, lo=5.0, hi=120.0)
        us = str(int(sec * 1_000_000))
        return {"timeout": us, "rw_timeout": us}

    def _log_mpv_network_debug_snapshot(self, *, media_key: str, url: str) -> None:
        """
        Best-effort debug snapshot for stubborn network streams.
        This helps distinguish 'mpv never tried to open URL' vs 'opened but blocked (403/TLS)'.
        """
        try:
            snap = self._pm._mpv_manager.get_properties_snapshot(
                [
                    "path",
                    "stream-open-filename",
                    "media-title",
                    "file-format",
                    "demuxer",
                    "idle-active",
                    "core-idle",
                    "time-pos",
                    "duration",
                ],
                timeout=2.0,
            )
            mpv_props = {
                k: snap.get(k)
                for k in (
                    "path",
                    "stream-open-filename",
                    "media-title",
                    "file-format",
                    "demuxer",
                )
            }
            idle = self._pm._snap_bool(snap, "idle-active")
            core_idle = self._pm._snap_bool(snap, "core-idle")
            tp = self._pm._snap_number(snap, "time-pos")
            dur = self._pm._snap_number(snap, "duration")
            self._pm.logger.warning(
                "MPV network stream debug snapshot",
                extra={
                    "media_key": media_key,
                    "url_preview": str(url)[:160],
                    "idle_active": idle,
                    "core_idle": core_idle,
                    "time_pos": tp,
                    "duration": dur,
                    "mpv_props": mpv_props,
                },
            )
        except Exception:
            # never let diagnostics break playback
            pass
