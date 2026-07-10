"""ENVIRONMENT.md presence and key coverage (backlog P-DOC)."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_DOC = _REPO_ROOT / "docs" / "ENVIRONMENT.md"


def test_environment_doc_exists():
    assert _ENV_DOC.is_file(), "docs/ENVIRONMENT.md missing"


def test_environment_doc_covers_key_vars():
    text = _ENV_DOC.read_text(encoding="utf-8")
    for var in (
        "DSIGN_API_TOKEN",
        "DSIGN_SHUTDOWN_JOIN_SEC",
        "DSIGN_RECOVERY_QUEUE_MAX",
        "DSIGN_CONTENT_CACHE_ENABLED",
        "DSIGN_MPV_RESTART_COALESCE_SEC",
    ):
        assert var in text, f"{var} not documented in ENVIRONMENT.md"


def test_environment_doc_covers_deployment_paths():
    text = _ENV_DOC.read_text(encoding="utf-8")
    assert "/etc/dsign/api.env" in text
    assert "wayland.env" in text
