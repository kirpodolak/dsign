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
    _parse_cli_args,
    _resolve_git_root,
    _units_restart_order,
    _working_tree_clean,
    apply_update,
    check_update,
    cmd_auto,
    download_update,
    load_rollback,
    save_rollback,
    sync_runtime_from_git,
)


def _cfg(
    tmp_path: Path,
    *,
    project_root: Path | None = None,
    runtime_root: Path | None = None,
) -> OtaConfig:
    repo = project_root or (tmp_path / "repo")
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    services = repo / "dsign" / "services"
    services.mkdir(parents=True, exist_ok=True)
    (services / "__init__.py").write_text("")
    (repo / "dsign" / "run.py").write_text("print('ok')\n")
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pip").write_text("#!/bin/sh\nexit 0\n")
    (venv / "pip").chmod(0o755)
    (repo / "requirements.txt").write_text("Flask>=3.0\n")
    runtime = runtime_root if runtime_root is not None else repo
    return OtaConfig(
        project_root=repo,
        runtime_root=runtime,
        venv_dir=tmp_path / "venv",
        ota_dir=tmp_path / "ota",
        branch="main",
        remote="origin",
        enabled=True,
        display_backend="drm",
        dsign_user="dsign",
    )


def _active_run_fn(calls: list[list[str]] | None = None):
    def run_fn(cmd, **kwargs):
        if calls is not None:
            calls.append(list(cmd))
        c = list(cmd)
        if len(c) >= 3 and c[0] == "systemctl" and c[1] == "is-active":
            return MagicMock(returncode=0, stdout="active\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return run_fn


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
        c = list(cmd)
        if len(c) >= 3 and c[0] == "systemctl" and c[1] == "is-active":
            return MagicMock(returncode=0, stdout="active\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("DSIGN_APPLY_INSTALL", "echo-apply")
    monkeypatch.setenv("DSIGN_OTA_SMOKE_CHECK", "0")

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    import dsign.services.ota_update as ota

    monkeypatch.setattr(ota.subprocess, "run", fake_run)

    result = apply_update(cfg, run_fn=run_fn)

    assert result["success"] is True
    assert any("pip" in " ".join(c) for c in calls)
    assert any(c[:2] == ["echo-apply", "-q"] for c in calls)
    assert result["restarted_units"][-1] == "digital-signage.service"
    assert result["runtime_sync"]["synced"] is False
    assert result["runtime_sync"]["reason"] == "same_tree"


def test_sync_runtime_copies_nested_package(tmp_path):
    git_root = tmp_path / "clone"
    runtime = tmp_path / "prod"
    pkg = git_root / "dsign"
    services = pkg / "services"
    services.mkdir(parents=True)
    (services / "playback_service.py").write_text("# v2\n")
    (pkg / "run.py").write_text("print('run')\n")

    cfg = _cfg(tmp_path, project_root=git_root, runtime_root=runtime)
    (pkg / "run.py").write_text("print('run')\n")

    def run_fn(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    result = sync_runtime_from_git(cfg, run_fn=run_fn)

    assert result["synced"] is True
    assert (runtime / "services" / "playback_service.py").read_text() == "# v2\n"
    assert (runtime / "run.py").read_text() == "print('run')\n"
    assert result["files_copied"] >= 2


def test_sync_runtime_skips_when_same_tree(tmp_path):
    cfg = _cfg(tmp_path)
    result = sync_runtime_from_git(cfg)
    assert result["synced"] is False
    assert result["reason"] == "same_tree"


def test_config_reads_runtime_root(tmp_path):
    outer = tmp_path / "home" / "dsign"
    repo = outer / "dsign-new"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    runtime = outer / "dsign"
    runtime.mkdir()
    cfg = OtaConfig.from_env(
        {
            "DSIGN_PROJECT_ROOT": str(repo),
            "DSIGN_RUNTIME_ROOT": str(runtime),
            "DSIGN_OTA_DIR": str(tmp_path / "ota"),
            "DSIGN_VENV": str(tmp_path / "venv"),
        }
    )
    assert cfg.project_root == repo.resolve()
    assert cfg.runtime_root == runtime.resolve()


def test_apply_syncs_separate_runtime_tree(tmp_path, monkeypatch):
    git_root = tmp_path / "clone"
    runtime = tmp_path / "prod"
    pkg = git_root / "dsign" / "services"
    pkg.mkdir(parents=True)
    (pkg / "app.py").write_text("x=1\n")

    cfg = _cfg(tmp_path, project_root=git_root, runtime_root=runtime)

    monkeypatch.setenv("DSIGN_APPLY_INSTALL", "echo-apply")
    monkeypatch.setenv("DSIGN_OTA_SMOKE_CHECK", "0")

    def run_fn(cmd, **kwargs):
        c = list(cmd)
        if len(c) >= 3 and c[0] == "systemctl" and c[1] == "is-active":
            return MagicMock(returncode=0, stdout="active\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    import dsign.services.ota_update as ota

    monkeypatch.setattr(ota.subprocess, "run", run_fn)

    result = apply_update(cfg, run_fn=run_fn)

    assert result["success"] is True
    assert result["runtime_sync"]["synced"] is True
    assert (runtime / "services" / "app.py").read_text() == "x=1\n"


def test_auto_skipped_when_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.enabled = False
    result = cmd_auto(cfg)
    assert result.get("skipped") is True


def test_config_runtime_defaults_to_project_root(tmp_path):
    repo = tmp_path / "dsign-new"
    repo.mkdir()
    (repo / ".git").mkdir()
    cfg = OtaConfig.from_env(
        {
            "DSIGN_PROJECT_ROOT": str(repo),
            "DSIGN_OTA_DIR": str(tmp_path / "ota"),
            "DSIGN_VENV": str(tmp_path / "venv"),
        }
    )
    assert cfg.project_root == repo.resolve()
    assert cfg.runtime_root == repo.resolve()


def test_restart_order_signage_last_drm():
    cfg = OtaConfig(
        project_root=Path("/tmp/r"),
        runtime_root=Path("/tmp/r"),
        venv_dir=Path("/tmp/v"),
        ota_dir=Path("/tmp/o"),
        branch="main",
        remote="origin",
        enabled=True,
        display_backend="drm",
        dsign_user="dsign",
    )
    assert _units_restart_order(cfg) == ["dsign-mpv.service", "digital-signage.service"]


def test_restart_order_signage_last_wayland():
    cfg = OtaConfig(
        project_root=Path("/tmp/r"),
        runtime_root=Path("/tmp/r"),
        venv_dir=Path("/tmp/v"),
        ota_dir=Path("/tmp/o"),
        branch="main",
        remote="origin",
        enabled=True,
        display_backend="wayland",
        dsign_user="dsign",
    )
    units = _units_restart_order(cfg)
    assert units[-1] == "digital-signage.service"
    assert "dsign-mpv-wayland.service" in units


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
            stdout="?? dsign/services/ota_update.py\n?? dsign/services/api_rate_limit.py\n?? docs/D1_OTA.md\n?? services/foo.py\n",
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
