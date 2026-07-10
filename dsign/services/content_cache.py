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
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Set

from .content_cache_prefetch import prefetch_workers
from .content_cache_retry import download_max_attempts, download_retry_delay_sec

_CACHE_KEY_RE = __import__("re").compile(r"^ext-[A-Za-z0-9_-]+$")


class ContentCache:
    def __init__(self, cache_dir: str, logger):
        self.logger = logger
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._prefetch_lock = Lock()
        self._active_prefetch: Dict[str, Future] = {}
        self._active_download_procs: Dict[str, subprocess.Popen] = {}
        self._cancelled_prefetch: Set[str] = set()
        self._prefetch_executor: Optional[ThreadPoolExecutor] = None
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

    def _get_prefetch_executor(self) -> ThreadPoolExecutor:
        if self._prefetch_executor is None:
            self._prefetch_executor = ThreadPoolExecutor(
                max_workers=prefetch_workers(),
                thread_name_prefix="content-cache-prefetch",
            )
        return self._prefetch_executor

    def cancel_prefetches(self, *, except_keys: Optional[Set[str]] = None) -> int:
        """Cancel in-flight prefetches (playlist change / stop)."""
        keep = except_keys or set()
        cancelled = 0
        with self._prefetch_lock:
            keys = [k for k in list(self._active_prefetch.keys()) if k not in keep]
            for key in keys:
                self._cancelled_prefetch.add(key)
                proc = self._active_download_procs.get(key)
                if proc is not None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                fut = self._active_prefetch.get(key)
                if fut is not None:
                    fut.cancel()
                cancelled += 1
        return cancelled

    def _is_prefetch_cancelled(self, media_key: str) -> bool:
        with self._prefetch_lock:
            return media_key in self._cancelled_prefetch

    def _clear_prefetch_cancelled(self, media_key: str) -> None:
        with self._prefetch_lock:
            self._cancelled_prefetch.discard(media_key)

    def _finish_prefetch(self, media_key: str) -> None:
        with self._prefetch_lock:
            self._active_prefetch.pop(media_key, None)
            self._active_download_procs.pop(media_key, None)
            self._cancelled_prefetch.discard(media_key)

    def shutdown_prefetch_pool(self, *, wait: bool = False) -> None:
        """Release prefetch worker threads (tests / process shutdown)."""
        self.cancel_prefetches()
        executor = self._prefetch_executor
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)
            self._prefetch_executor = None

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
            fut = self._active_prefetch.get(key)
            if fut is not None and not fut.done():
                return False
            self._clear_prefetch_cancelled(key)
        future = self._get_prefetch_executor().submit(
            self._prefetch_worker,
            key,
            url,
            str(provider or ""),
        )
        with self._prefetch_lock:
            self._active_prefetch[key] = future
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
            "active_prefetches": sum(
                1 for fut in self._active_prefetch.values() if fut is not None and not fut.done()
            ),
        }

    # ------------------------------------------------------------------ workers

    def _prefetch_worker(self, media_key: str, page_url: str, provider: str) -> None:
        try:
            if self._is_prefetch_cancelled(media_key):
                return
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
            self._finish_prefetch(media_key)

    def _wait_download_retry(self, media_key: str, delay_sec: float) -> bool:
        """Sleep until delay elapses unless prefetch was cancelled."""
        deadline = time.monotonic() + max(0.0, float(delay_sec))
        while time.monotonic() < deadline:
            if self._is_prefetch_cancelled(media_key):
                return False
            time.sleep(min(0.25, deadline - time.monotonic()))
        return not self._is_prefetch_cancelled(media_key)

    def _download(self, media_key: str, page_url: str, provider: str) -> bool:
        if self._is_prefetch_cancelled(media_key):
            return False
        if self.is_ready(media_key):
            return True
        ytdl = self._ytdl_path()
        if not os.path.isfile(ytdl):
            self.logger.warning("Content cache: yt-dlp missing", extra={"path": ytdl})
            return False

        max_attempts = download_max_attempts()
        for attempt in range(1, max_attempts + 1):
            if self._is_prefetch_cancelled(media_key):
                return False
            if self._download_once(media_key, page_url, provider):
                return True
            if attempt >= max_attempts:
                self.logger.warning(
                    "Content cache: download exhausted retries",
                    extra={"media_key": media_key, "attempts": attempt},
                )
                return False
            delay = download_retry_delay_sec(attempt)
            self.logger.info(
                "Content cache: download retry scheduled",
                extra={
                    "media_key": media_key,
                    "next_attempt": attempt + 1,
                    "delay_sec": round(delay, 2),
                },
            )
            if not self._wait_download_retry(media_key, delay):
                return False
        return False

    def _download_once(self, media_key: str, page_url: str, provider: str) -> bool:
        if self._is_prefetch_cancelled(media_key):
            return False
        out_path = self._media_path(media_key)
        part_path = self._part_path(media_key)
        if self.is_ready(media_key):
            return True
        part_path.unlink(missing_ok=True)
        ytdl = self._ytdl_path()
        if not os.path.isfile(ytdl):
            self.logger.warning("Content cache: yt-dlp missing", extra={"path": ytdl})
            return False
        tmpl = str(part_path.with_suffix(".%(ext)s"))
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
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        with self._prefetch_lock:
            self._active_download_procs[media_key] = proc
        deadline = time.monotonic() + timeout
        return_code = None
        try:
            while True:
                if self._is_prefetch_cancelled(media_key):
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    part_path.unlink(missing_ok=True)
                    return False
                if proc.poll() is not None:
                    return_code = int(proc.returncode or 0)
                    break
                if time.monotonic() >= deadline:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    part_path.unlink(missing_ok=True)
                    self.logger.warning(
                        "Content cache: yt-dlp download timed out",
                        extra={"media_key": media_key},
                    )
                    return False
                time.sleep(0.25)
        finally:
            with self._prefetch_lock:
                self._active_download_procs.pop(media_key, None)
        stderr_tail = ""
        try:
            _, stderr_data = proc.communicate(timeout=1.0)
            stderr_tail = (stderr_data or "")[-400:]
        except Exception:
            pass
        if return_code != 0:
            part_path.unlink(missing_ok=True)
            for stray in self.cache_dir.glob(f"{media_key}.*"):
                if stray.suffix in (".part", ".temp", ".ytdl"):
                    stray.unlink(missing_ok=True)
            self.logger.warning(
                "Content cache: yt-dlp download failed",
                extra={
                    "media_key": media_key,
                    "stderr": stderr_tail,
                },
            )
            return False
        downloaded = part_path if part_path.is_file() else None
        if downloaded is None:
            for cand in self.cache_dir.glob(f"{media_key}.*"):
                if cand.suffix.lower() in (".mp4", ".mkv", ".webm") and cand.is_file():
                    downloaded = cand
                    break
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
