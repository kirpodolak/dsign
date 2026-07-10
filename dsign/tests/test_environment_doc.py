"""ENVIRONMENT.md presence and key coverage (backlog P-DOC)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_DOC = REPO_ROOT / "docs" / "ENVIRONMENT.md"

REQUIRED_VARS = (
    "DSIGN_API_TOKEN",
    "DSIGN_SHUTDOWN_JOIN_SEC",
    "DSIGN_RECOVERY_QUEUE_MAX",
    "DSIGN_CONTENT_CACHE_ENABLED",
    "DSIGN_MPV_RESTART_COALESCE_SEC",
    "DSIGN_UPLOAD_STREAM_THRESHOLD_BYTES",
    "DSIGN_MEDIA_BACKOFF_TTL_SEC",
)


def test_environment_md_exists():
    assert ENV_DOC.is_file()


def test_environment_md_documents_key_vars():
    text = ENV_DOC.read_text(encoding="utf-8")
    for var in REQUIRED_VARS:
        assert var in text, f"missing {var} in ENVIRONMENT.md"


def test_environment_md_has_deployment_paths():
    text = ENV_DOC.read_text(encoding="utf-8")
    assert "/etc/dsign/api.env" in text
    assert "wayland.env" in text
