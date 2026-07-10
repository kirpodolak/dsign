"""Upload disk space checks (backlog H-UPL)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dsign.services.file_service import FileService
from dsign.services.upload_disk import (
    DEFAULT_UPLOAD_DISK_RESERVE_BYTES,
    check_disk_space_for_upload,
    upload_disk_reserve_bytes,
)


def test_upload_disk_reserve_bytes_default():
    assert upload_disk_reserve_bytes() == DEFAULT_UPLOAD_DISK_RESERVE_BYTES


def test_upload_disk_reserve_bytes_env(monkeypatch):
    monkeypatch.setenv("DSIGN_UPLOAD_DISK_RESERVE_BYTES", "1048576")
    assert upload_disk_reserve_bytes() == 1048576


def test_check_disk_space_accepts_when_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dsign.services.upload_disk.shutil.disk_usage",
        lambda _p: MagicMock(free=100 * 1024 * 1024, total=0, used=0),
    )
    monkeypatch.setenv("DSIGN_UPLOAD_DISK_RESERVE_BYTES", "0")

    ok, err = check_disk_space_for_upload(tmp_path, 10 * 1024 * 1024)

    assert ok is True
    assert err is None


def test_check_disk_space_rejects_known_size(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dsign.services.upload_disk.shutil.disk_usage",
        lambda _p: MagicMock(free=5 * 1024 * 1024, total=0, used=0),
    )
    monkeypatch.setenv("DSIGN_UPLOAD_DISK_RESERVE_BYTES", "0")

    ok, err = check_disk_space_for_upload(tmp_path, 10 * 1024 * 1024)

    assert ok is False
    assert "Insufficient disk space" in (err or "")
    assert "5 MiB free" in (err or "")


def test_check_disk_space_unknown_size_uses_max_file_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dsign.services.upload_disk.shutil.disk_usage",
        lambda _p: MagicMock(free=20 * 1024 * 1024, total=0, used=0),
    )
    monkeypatch.setenv("DSIGN_UPLOAD_DISK_RESERVE_BYTES", "0")

    ok, err = check_disk_space_for_upload(
        tmp_path,
        0,
        max_file_bytes=50 * 1024 * 1024,
    )

    assert ok is False
    assert "50 MiB" in (err or "")


def test_handle_upload_skips_file_when_disk_low(null_logger, tmp_path, monkeypatch):
    svc = FileService(str(tmp_path), logger=null_logger)

    monkeypatch.setattr(
        "dsign.services.file_service.check_disk_space_for_upload",
        lambda *_a, **_k: (False, "Insufficient disk space: 1 MiB free, need at least 10 MiB for this upload"),
    )

    storage = MagicMock()
    storage.filename = "clip.mp4"
    storage.content_length = 10 * 1024 * 1024

    saved = svc.handle_upload([storage])

    assert saved == []
    storage.save.assert_not_called()


def test_handle_upload_saves_when_disk_ok(null_logger, tmp_path, monkeypatch):
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
    assert (tmp_path / "photo.jpg").is_file()
