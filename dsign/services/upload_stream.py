"""Chunked media upload for large files (backlog H-UPL tail)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, BinaryIO, Optional

DEFAULT_UPLOAD_STREAM_THRESHOLD_BYTES = 100 * 1024 * 1024  # 100 MiB
DEFAULT_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB


class UploadTooLargeError(Exception):
    """Raised when streamed upload exceeds ``max_bytes``."""

    def __init__(self, bytes_written: int):
        super().__init__(f"upload exceeds max size after {bytes_written} bytes")
        self.bytes_written = int(bytes_written)


def upload_stream_threshold_bytes() -> int:
    raw = (os.getenv("DSIGN_UPLOAD_STREAM_THRESHOLD_BYTES") or "").strip()
    if raw.isdigit():
        return max(1024, int(raw))
    return DEFAULT_UPLOAD_STREAM_THRESHOLD_BYTES


def upload_chunk_bytes() -> int:
    raw = (os.getenv("DSIGN_UPLOAD_CHUNK_BYTES") or "").strip()
    if raw.isdigit():
        return max(16 * 1024, min(16 * 1024 * 1024, int(raw)))
    return DEFAULT_UPLOAD_CHUNK_BYTES


def upload_size_hint(file: Any) -> Optional[int]:
    """Return Content-Length when present — never seek the request body."""
    cl = getattr(file, "content_length", None)
    if cl is not None and cl > 0:
        return int(cl)
    return None


def should_stream_upload(
    size_hint: Optional[int],
    *,
    threshold_bytes: Optional[int] = None,
) -> bool:
    """
    Use chunked streaming when size is unknown or at/above threshold.

    Unknown sizes skip seek-based probing so the body is not buffered in RAM.
    """
    threshold = upload_stream_threshold_bytes() if threshold_bytes is None else int(threshold_bytes)
    if size_hint is None or size_hint <= 0:
        return True
    return int(size_hint) >= threshold


def stream_save_upload(
    stream: BinaryIO,
    dest: Path,
    *,
    max_bytes: int,
    chunk_size: Optional[int] = None,
) -> int:
    """Write ``stream`` to ``dest`` in fixed-size chunks; enforce ``max_bytes``."""
    chunk = upload_chunk_bytes() if chunk_size is None else int(chunk_size)
    dest.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    try:
        with open(dest, "wb") as out:
            while True:
                data = stream.read(chunk)
                if not data:
                    break
                written += len(data)
                if written > int(max_bytes):
                    raise UploadTooLargeError(written)
                out.write(data)
    except UploadTooLargeError:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return written
