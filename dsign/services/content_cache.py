"""
Disk cache for external (network) playlist items — C1 ContentCache.

Downloads Rutube/VK page URLs on the signage device (same egress as mpv ytdl_hook),
prefetches the next item during playback, and serves local files when offline or ready.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, Optional

_CACHE_KEY_RE = __import__("re").compile(r"^ext-[A-Za-z0-9_-]+$")


class ContentCache:
    def __init__(self, cache_dir: str, logger):
        self.logger = logger
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._prefetch_lock = Lock()
        self._active_prefetch: Dict[str, Thread] = {}
        self._internet_cache: Dict[str, Any] = {"ok": None, "ts": 0.0}

    # ------------------------------------------------------------------ config

    def enabled(self) -> bool:
        v = (os.getenv("DSIGN_CONTENT_CACHE_ENABLED") or "1").strip().lower()
        return v not in ("0", "false", "no", "off")

    def prefetch_enabled(self) -> bool:
        if not self.enabled():
            return False
        v = (os.getenv("DSIGN_CONTENT_CACHE_PREFETCH") or "1").strip().lower()
        return v not in ("0", "false", "no", "off")

    def play_when_ready(self) -> bool:
        v = (os.getenv("DSIGN_CONTENT_CACHE_PLAY_WHEN_READY") or "1").strip().lower()
        return v not in ("0", "false", "no", "off")

    def _max_bytes(self) -> int:
        try:
            gb = float((os.getenv("DSIGN_CONTENT_CACHE_MAX_GB") or "8").strip())
        except ValueError:
            gb = 8.0
        gb = max(0.5, min(128.0, gb))
        return int(gb * 1024 * 1024 * 1024)

    def _ytdl_path(self) -> str:
        return (os.getenv("DSIGN_YTDLP_PATH") or "/usr/bin/yt-dlp").strip() or "/usr/bin/yt-dlp"

    # ------------------------------------------------------------------ paths

    def _safe_key(self, media_key: str) -> Optional[str]:
        key = str(media_key or "").strip()
        if not key or not _CACHE_KEY_RE.match(key):
            return None
        return key

    def _media_path(self, media_key: str) -> Path:
        return self.cache_dir / f"{media_key}.mp4"

    def _meta_path(self, media_key: str) -> Path:
        return self.cache_dir / f"{media_key}.json"

    def _part_path(self, media_key: str) -> Path:
        return self.cache_dir / f"{media_key}.mp4.part"

    # ------------------------------------------------------------------ public

    def has_internet(self, *, force: bool = False) -> bool:
        now = time.time()
        if not force and self._internet_cache.get("ok") is not None:
            if (now - float(self._internet_cache.get("ts") or 0.0)) < 8.0:
                return bool(self._internet_cache["ok"])
        probes = (("1.1.1.1", 53), ("8.8.8.8", 53))
        ok = False
        for host, port in probes:
            try:
                with socket.create_connection((host, port), timeout=1.2):
                    ok = True
                    break
            except OSError:
                continue
        self._internet_cache = {"ok": ok, "ts": now}
        return ok

    def is_ready(self, media_key: str) -> bool:
        key = self._safe_key(media_key)
        if not key:
            return False
        path = self._media_path(key)
        if not path.is_file() or path.stat().st_size < 65536:
            return False
        return self._ffprobe_ok(path)

    def get_local_path(self, media_key: str) -> Optional[Path]:
        if not self.is_ready(media_key):
            return None
        return self._media_path(str(media_key))

    def should_use_cache_for_playback(self, media_key: str) -> bool:
        if not self.enabled() or not self.is_ready(media_key):
            return False
        if not self.has_internet():
            return True
        return self.play_when_ready()

    def build_playback_dict(self, media_key: str, *, page_url: str = "", provider: str = "") -> Optional[Dict[str, Any]]:
        """Return a playlist item path dict when disk cache should be used."""
        key = self._safe_key(media_key)
        if not key or not self.should_use_cache_for_playback(key):
            return None
        path = self.get_local_path(key)
        if path is None:
            return None
        meta = self._read_meta(key) or {}
        return {
            "key": key,
            "path": str(path),
            "is_video": True,
            "http_headers": {},
            "page_url": page_url or meta.get("page_url") or "",
            "provider": provider or meta.get("provider") or "",
            "from_content_cache": True,
        }

    def prefetch_async(
        self,
        *,
        media_key: str,
        page_url: str,
        provider: str = "",
    ) -> bool:
        if not self.prefetch_enabled():
            return False
        key = self._safe_key(media_key)
        url = str(page_url or "").strip()
        if not key or not url.startswith(("http://", "https://")):
            return False
        if self.is_ready(key):
            return False
        with self._prefetch_lock:
            t = self._active_prefetch.get(key)
            if t is not None and t.is_alive():
                return False
            thread = Thread(
                target=self._prefetch_worker,
                name=f"content-cache-{key}",
                args=(key, url, str(provider or "")),
                daemon=True,
            )
            self._active_prefetch[key] = thread
            thread.start()
        return True

    def get_status_summary(self) -> Dict[str, Any]:
        ready = 0
        total_bytes = 0
        try:
            for p in self.cache_dir.glob("ext-*.mp4"):
                if p.is_file():
                    total_bytes += p.stat().st_size
                    key = p.stem
                    if self.is_ready(key):
                        ready += 1
        except Exception:
            pass
        return {
            "enabled": self.enabled(),
            "prefetch_enabled": self.prefetch_enabled(),
            "internet_online": self.has_internet() if self.enabled() else None,
            "cached_items": ready,
            "cache_size_mb": round(total_bytes / (1024 * 1024), 1),
            "cache_dir": str(self.cache_dir),
        }

    # ------------------------------------------------------------------ workers

    def _prefetch_worker(self, media_key: str, page_url: str, provider: str) -> None:
        try:
            if not self.has_internet(force=True):
                return
            self._download(media_key, page_url, provider)
        except Exception as e:
            self.logger.warning(
                "Content cache prefetch failed",
                extra={
                    "media_key": media_key,
                    "error": str(e),
                    "type": type(e).__name__,
                },
            )
        finally:
            with self._prefetch_lock:
                self._active_prefetch.pop(media_key, None)

    def _download(self, media_key: str, page_url: str, provider: str) -> bool:
        out_path = self._media_path(media_key)
        if self.is_ready(media_key):
            return True
        self._cleanup_partial_downloads(media_key)
        ytdl = self._ytdl_path()
        if not os.path.isfile(ytdl):
            self.logger.warning("Content cache: yt-dlp missing", extra={"path": ytdl})
            return False
        # yt-dlp appends .%(ext)s — do NOT use ext-N.mp4 as basename (becomes ext-N.mp4.mp4).
        tmpl = str(self.cache_dir / f"{media_key}.%(ext)s")
        cmd = [
            ytdl,
            "--no-warnings",
            "--no-update",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            tmpl,
            page_url,
        ]
        self.logger.info(
            "Content cache: download started",
            extra={"media_key": media_key, "page_url": page_url[:120]},
        )
        try:
            timeout = float((os.getenv("DSIGN_CONTENT_CACHE_DOWNLOAD_SEC") or "7200").strip())
        except ValueError:
            timeout = 7200.0
        timeout = max(120.0, min(14400.0, timeout))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            self._cleanup_partial_downloads(media_key)
            self.logger.warning(
                "Content cache: yt-dlp download failed",
                extra={
                    "media_key": media_key,
                    "stderr": (proc.stderr or "")[-400:],
                },
            )
            return False
        downloaded = self._find_downloaded_file(media_key)
        if downloaded is None or not self._ffprobe_ok(downloaded):
            self.logger.warning(
                "Content cache: downloaded file invalid",
                extra={"media_key": media_key},
            )
            if downloaded:
                downloaded.unlink(missing_ok=True)
            return False
        if downloaded != out_path:
            downloaded.replace(out_path)
        meta = {
            "media_key": media_key,
            "page_url": page_url,
            "provider": provider,
            "ready_at": int(time.time()),
            "size": out_path.stat().st_size,
        }
        self._write_meta(media_key, meta)
        self._enforce_size_limit()
        self.logger.info(
            "Content cache: ready",
            extra={"media_key": media_key, "size_mb": round(out_path.stat().st_size / (1024 * 1024), 2)},
        )
        return True

    def _cleanup_partial_downloads(self, media_key: str) -> None:
        """Remove yt-dlp partials (including legacy ext-N.mp4.mp4.* names)."""
        patterns = (
            f"{media_key}.*",
            f"{media_key}.mp4.*",
        )
        seen: set[str] = set()
        for pat in patterns:
            for path in self.cache_dir.glob(pat):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                name = path.name
                if path == self._media_path(media_key):
                    continue
                if name.endswith((".part", ".ytdl")) or ".part-" in name:
                    path.unlink(missing_ok=True)
                elif name in (f"{media_key}.mp4.mp4", f"{media_key}.webm", f"{media_key}.mkv"):
                    path.unlink(missing_ok=True)

    def _find_downloaded_file(self, media_key: str) -> Optional[Path]:
        preferred = self._media_path(media_key)
        if preferred.is_file():
            return preferred
        candidates = []
        for cand in self.cache_dir.glob(f"{media_key}.*"):
            if not cand.is_file():
                continue
            if cand.suffix.lower() not in (".mp4", ".mkv", ".webm"):
                continue
            if ".part" in cand.name:
                continue
            candidates.append(cand)
        # Prefer mp4; accept legacy double-extension from pre-fix builds.
        candidates.sort(key=lambda p: (p.suffix.lower() != ".mp4", len(p.name), p.name))
        return candidates[0] if candidates else None

    def _enforce_size_limit(self) -> None:
        max_bytes = self._max_bytes()
        files = []
        for p in self.cache_dir.glob("ext-*.mp4"):
            try:
                if p.is_file():
                    files.append((p.stat().st_mtime, p.stat().st_size, p))
            except OSError:
                continue
        files.sort(key=lambda x: x[0])
        total = sum(sz for _, sz, _ in files)
        while files and total > max_bytes:
            _, sz, path = files.pop(0)
            key = path.stem
            path.unlink(missing_ok=True)
            self._meta_path(key).unlink(missing_ok=True)
            total -= sz
            self.logger.info("Content cache: evicted", extra={"media_key": key})

    def _read_meta(self, media_key: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self._meta_path(media_key).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_meta(self, media_key: str, payload: Dict[str, Any]) -> None:
        try:
            self._meta_path(media_key).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _ffprobe_ok(self, path: Path) -> bool:
        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=12,
            )
            return proc.returncode == 0 and "video" in (proc.stdout or "").lower()
        except Exception:
            return False
