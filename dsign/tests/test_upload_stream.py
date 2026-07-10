"""Streaming upload helpers (backlog H-UPL tail)."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dsign.services.file_service import FileService
from dsign.services.upload_stream import (
    DEFAULT_UPLOAD_CHUNK_BYTES,
    DEFAULT_UPLOAD_STREAM_THRESHOLD_BYTES,
    UploadTooLargeError,
    should_stream_upload,
    stream_save_upload,
    upload_chunk_bytes,
    upload_size_hint,
    upload_stream_threshold_bytes,
)


def test_upload_stream_threshold_bytes_default():
    assert upload_stream_threshold_bytes() == DEFAULT_UPLOAD_STREAM_THRESHOLD_BYTES


def test_upload_stream_threshold_bytes_env(monkeypatch):
    monkeypatch.setenv("DSIGN_UPLOAD_STREAM_THRESHOLD_BYTES", "2097152")
    assert upload_stream_threshold_bytes() == 2097152


def test_upload_chunk_bytes_default():
    assert upload_chunk_bytes() == DEFAULT_UPLOAD_CHUNK_BYTES


def test_upload_size_hint_uses_content_length_only():
    storage = MagicMock()
    storage.content_length = 42
    storage.stream = io.BytesIO(b"x" * 42)

    assert upload_size_hint(storage) == 42
    storage.stream.seek(0, 2)  # would change tell if seek were used
    assert upload_size_hint(storage) == 42


def test_should_stream_upload_unknown_size():
    assert should_stream_upload(None) is True
    assert should_stream_upload(0) is True


def test_should_stream_upload_large_known_size():
    threshold = 100 * 1024 * 1024
    assert should_stream_upload(threshold - 1, threshold_bytes=threshold) is False
    assert should_stream_upload(threshold, threshold_bytes=threshold) is True


def test_stream_save_upload_writes_in_chunks(tmp_path):
    payload = b"a" * (3 * 1024 + 10)
    stream = io.BytesIO(payload)

    written = stream_save_upload(stream, tmp_path / "big.bin", max_bytes=len(payload), chunk_size=1024)

    assert written == len(payload)
    assert (tmp_path / "big.bin").read_bytes() == payload


def test_stream_save_upload_rejects_oversize(tmp_path):
    stream = io.BytesIO(b"x" * 2048)

    with pytest.raises(UploadTooLargeError) as exc:
        stream_save_upload(stream, tmp_path / "big.bin", max_bytes=1024, chunk_size=512)

    assert exc.value.bytes_written == 1536
    assert not (tmp_path / "big.bin").exists()


def test_handle_upload_streams_large_file_without_seek(null_logger, tmp_path, monkeypatch):
    svc = FileService(str(tmp_path), logger=null_logger)
    monkeypatch.setenv("DSIGN_UPLOAD_STREAM_THRESHOLD_BYTES", "1024")
    monkeypatch.setattr(
        "dsign.services.file_service.check_disk_space_for_upload",
        lambda *_a, **_k: (True, None),
    )

    payload = b"v" * 2048
    storage = MagicMock()
    storage.filename = "large.mp4"
    storage.content_length = len(payload)
    storage.stream = io.BytesIO(payload)
    storage.save = MagicMock()

    saved = svc.handle_upload([storage])

    assert saved == ["large.mp4"]
    assert (tmp_path / "large.mp4").stat().st_size == len(payload)
    storage.save.assert_not_called()


def test_handle_upload_small_known_file_uses_save(null_logger, tmp_path, monkeypatch):
    svc = FileService(str(tmp_path), logger=null_logger)
    monkeypatch.setattr(
        "dsign.services.file_service.check_disk_space_for_upload",
        lambda *_a, **_k: (True, None),
    )

    storage = MagicMock()
    storage.filename = "photo.jpg"
    storage.content_length = 1024

    def _save(path: str) -> None:
        Path(path).write_bytes(b"x" * 1024)

    storage.save.side_effect = _save

    saved = svc.handle_upload([storage])

    assert saved == ["photo.jpg"]
    storage.save.assert_called_once()


def test_handle_upload_unknown_size_streams_without_seek(null_logger, tmp_path, monkeypatch):
    svc = FileService(str(tmp_path), logger=null_logger)
    monkeypatch.setattr(
        "dsign.services.file_service.check_disk_space_for_upload",
        lambda *_a, **_k: (True, None),
    )

    payload = b"z" * 4096
    storage = MagicMock()
    storage.filename = "clip.mp4"
    storage.content_length = None
    storage.stream = io.BytesIO(payload)
    storage.save = MagicMock()

    saved = svc.handle_upload([storage])

    assert saved == ["clip.mp4"]
    assert (tmp_path / "clip.mp4").read_bytes() == payload
    storage.save.assert_not_called()
