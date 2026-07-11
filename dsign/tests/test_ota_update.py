"""Unit tests for OTA self-update (backlog D1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dsign.services.ota_update import (
    OtaConfig,
    _build_parser,
    _clear_pycache,
    _parse_cli_args,
    _resolve_git_root,
    _working_tree_clean,
    apply_update,
    check_update,
    cmd_auto,
    download_update,
    load_rollback,
    purge_pycache,
    save_rollback,
)


def _cfg(tmp_path: Path) -> OtaConfig:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "pip").write_text("#!/bin/sh\nexit 0\n")
    (venv / "pip").chmod(0o755)
    (repo / "requirements.txt").write_text("Flask>=3.0\n")
    return OtaConfig(
        project_root=repo,
        venv_dir=tmp_path / "venv",
        ota_dir=tmp_path / "ota",
        branch="main",
        remote="origin",
        enabled=True,
        display_backend="drm",
        dsign_user="dsign",
    )


def _git_mock(responses: dict[tuple, subprocess.CompletedProcess]):
    def _run(cmd, **kwargs):
        key = tuple(cmd)
        if key in responses:
            return responses[key]
        return MagicMock(returncode=0, stdout="", stderr="")

    return _run


def test_check_reports_up_to_date(tmp_path):
    cfg = _cfg(tmp_path)
    local = "a" * 40
    run_fn = _git_mock(
        {
            ("sudo", "-n", "-H", "-u", "dsign", "git", "-C", str(cfg.project_root), "fetch", "origin", "main"): MagicMock(
                returncode=0, stdout="", stderr=""
            ),
            ("sudo", "-n", "-H", "-u", "dsign", "git", "-C", str(cfg.project_root), "rev-parse", "HEAD"): MagicMock(
                returncode=0, stdout=local + "\n", stderr=""
            ),
            (
                "sudo",
                "-n",
                "-H",
                "-u",
                "dsign",
                "git",
                "-C",
                str(cfg.project_root),
                "rev-parse",
                "origin/main",
            ): MagicMock(returncode=0, stdout=local + "\n", stderr=""),
        }
    )

    result = check_update(cfg, run_fn=run_fn)

    assert result["update_available"] is False
    assert result["local_commit"] == local


def test_check_reports_update_available(tmp_path):
    cfg = _cfg(tmp_path)
    local = "a" * 40
    remote = "b" * 40
    run_fn = _git_mock(
        {
            ("sudo", "-n", "-H", "-u", "dsign", "git", "-C", str(cfg.project_root), "fetch", "origin", "main"): MagicMock(
                returncode=0, stdout="", stderr=""
            ),
            ("sudo", "-n", "-H", "-u", "dsign", "git", "-C", str(cfg.project_root), "rev-parse", "HEAD"): MagicMock(
                returncode=0, stdout=local + "\n", stderr=""
            ),
            (
                "sudo",
                "-n",
                "-H",
                "-u",
                "dsign",
                "git",
                "-C",
                str(cfg.project_root),
                "rev-parse",
                "origin/main",
            ): MagicMock(returncode=0, stdout=remote + "\n", stderr=""),
        }
    )

    result = check_update(cfg, run_fn=run_fn)

    assert result["update_available"] is True
    assert result["remote_commit"] == remote


def test_download_saves_rollback_and_merges(tmp_path):
    cfg = _cfg(tmp_path)
    local = "a" * 40
    remote = "b" * 40

    def run_fn(cmd, **kwargs):
        c = tuple(cmd)
        if c[-2:] == ("rev-parse", "HEAD"):
            return MagicMock(returncode=0, stdout=remote + "\n", stderr="")
        if c[-1:] == ("main",) and "fetch" in c:
            return MagicMock(returncode=0, stdout="", stderr="")
        if c[-1:] == ("origin/main",):
            return MagicMock(returncode=0, stdout=remote + "\n", stderr="")
        if c[-1:] == ("--porcelain",):
            return MagicMock(returncode=0, stdout="", stderr="")
        if c[-3:] == ("--ff-only", "origin/main"):
            return MagicMock(returncode=0, stdout="", stderr="")
        if c[-2:] == ("rev-parse", "HEAD"):
            return MagicMock(returncode=0, stdout=remote + "\n", stderr="")
        return MagicMock(returncode=0, stdout=local + "\n", stderr="")

    # First call returns local for HEAD before merge
    calls = {"head": 0}

    def smarter_run(cmd, **kwargs):
        c = tuple(cmd)
        if c[-2:] == ("rev-parse", "HEAD"):
            calls["head"] += 1
            val = local if calls["head"] == 1 else remote
            return MagicMock(returncode=0, stdout=val + "\n", stderr="")
        if "fetch" in c:
            return MagicMock(returncode=0, stdout="", stderr="")
        if c[-1:] == ("origin/main",):
            return MagicMock(returncode=0, stdout=remote + "\n", stderr="")
        if c[-1:] == ("--porcelain",):
            return MagicMock(returncode=0, stdout="", stderr="")
        if c[-3:] == ("--ff-only", "origin/main"):
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    result = download_update(cfg, run_fn=smarter_run)

    assert result["downloaded"] is True
    rb = load_rollback(cfg)
    assert rb is not None
    assert rb["previous_commit"] == local


def test_save_rollback_persists(tmp_path):
    cfg = _cfg(tmp_path)
    save_rollback(cfg, "deadbeef" * 5, "main")
    rb = load_rollback(cfg)
    assert rb["previous_commit"] == "deadbeef" * 5
    assert rb["branch"] == "main"


def test_apply_runs_pip_manifest_and_restart(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    calls: list[list[str]] = []

    def run_fn(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("DSIGN_APPLY_INSTALL", "echo-apply")

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    import dsign.services.ota_update as ota

    monkeypatch.setattr(ota.subprocess, "run", fake_run)

    result = apply_update(cfg, run_fn=run_fn)

    assert result["success"] is True
    assert any("pip" in " ".join(c) for c in calls)
    assert any(c[:2] == ["echo-apply", "-q"] for c in calls)
    assert "digital-signage.service" in result["restarted_units"]


def test_auto_skipped_when_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.enabled = False
    result = cmd_auto(cfg)
    assert result.get("skipped") is True


def test_config_reads_ota_env_file(tmp_path):
    env_file = tmp_path / "ota.env"
    env_file.write_text("DSIGN_OTA_BRANCH=stable\nDSIGN_OTA_ENABLED=0\n")
    cfg = OtaConfig.from_env({"DSIGN_OTA_ENV": str(env_file), "DSIGN_OTA_DIR": str(tmp_path / "ota")})
    assert cfg.branch == "stable"
    assert cfg.enabled is False


def test_cli_json_flag_after_subcommand():
    args = _parse_cli_args(["check", "--json"])
    assert args.command == "check"
    assert args.json is True


def test_working_tree_ignores_bootstrap_ota_files(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def fake_run(cmd, **kwargs):
        return MagicMock(
            returncode=0,
            stdout="?? dsign/services/ota_update.py\n?? services/ota_update.py\n",
            stderr="",
        )

    assert _working_tree_clean(cfg, run_fn=fake_run) is True


def test_working_tree_blocks_other_local_changes(tmp_path):
    cfg = _cfg(tmp_path)

    def fake_run(cmd, **kwargs):
        return MagicMock(returncode=0, stdout=" M dsign/routes/api.py\n", stderr="")

    assert _working_tree_clean(cfg, run_fn=fake_run) is False


def test_resolve_git_root_finds_clone(tmp_path):
    repo = tmp_path / "dsign"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert _resolve_git_root(repo) == repo.resolve()


def test_config_uses_git_root(tmp_path):
    outer = tmp_path / "home" / "dsign"
    repo = outer / "dsign"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    cfg = OtaConfig.from_env(
        {
            "DSIGN_PROJECT_ROOT": str(outer),
            "DSIGN_OTA_DIR": str(tmp_path / "ota"),
            "DSIGN_VENV": str(tmp_path / "venv"),
        }
    )
    assert cfg.project_root == repo.resolve()


def test_cli_json_flag_before_subcommand():
    args = _parse_cli_args(["--json", "status"])
    assert args.command == "status"
    assert args.json is True


def test_version_reports_tool_version(capsys):
    args = _parse_cli_args(["version", "--json"])
    assert args.command == "version"
    from dsign.services.ota_update import OTA_TOOL_VERSION, main

    assert main(["version", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"tool_version"' in out
    assert OTA_TOOL_VERSION


def test_cli_json_from_sys_argv(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ota_update.py", "version", "--json"])
    from dsign.services.ota_update import main

    assert main() == 0
    assert '"tool_version"' in capsys.readouterr().out


def test_clear_pycache_removes_bytecode(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / "dsign" / "services" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "playlist_management.cpython-312.pyc").write_bytes(b"fake")
    assert _clear_pycache(repo) == 1
    assert not cache.exists()


def test_purge_pycache_cli(tmp_path):
    cfg = _cfg(tmp_path)
    cache = cfg.project_root / "dsign" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"x")
    result = purge_pycache(cfg)
    assert result["success"] is True
    assert result["pycache_entries_removed"] == 1
    assert not cache.exists()


def test_apply_clears_pycache_before_restart(tmp_path):
    cfg = _cfg(tmp_path)
    cache = cfg.project_root / "dsign" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"x")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    result = apply_update(cfg, run_fn=fake_run)
    assert result["pycache_entries_removed"] == 1
    assert not cache.exists()
