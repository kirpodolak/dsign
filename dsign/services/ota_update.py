"""DSign OTA self-update (backlog D1): git fetch + apply-install + service restart."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_PROJECT_ROOT = Path("/home/dsign/dsign")
DEFAULT_VENV = Path("/home/dsign/venv")
DEFAULT_OTA_DIR = Path("/var/lib/dsign/ota")
DEFAULT_OTA_ENV = Path("/etc/dsign/ota.env")
DEFAULT_BRANCH = "main"
DEFAULT_REMOTE = "origin"
DSIGN_USER = "dsign"
SIGNAGE_UNIT = "digital-signage.service"

RunFn = Callable[..., subprocess.CompletedProcess]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


@dataclass
class OtaConfig:
    project_root: Path
    venv_dir: Path
    ota_dir: Path
    branch: str
    remote: str
    enabled: bool
    display_backend: str
    dsign_user: str

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "OtaConfig":
        base = dict(env if env is not None else os.environ)
        ota_env_path = Path(
            base.get("DSIGN_OTA_ENV", os.environ.get("DSIGN_OTA_ENV", str(DEFAULT_OTA_ENV)))
        )
        merged = {**_load_env_file(ota_env_path), **base}
        ota_dir = Path(merged.get("DSIGN_OTA_DIR", str(DEFAULT_OTA_DIR)))
        return cls(
            project_root=Path(merged.get("DSIGN_PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))),
            venv_dir=Path(merged.get("DSIGN_VENV", str(DEFAULT_VENV))),
            ota_dir=ota_dir,
            branch=(merged.get("DSIGN_OTA_BRANCH", DEFAULT_BRANCH) or DEFAULT_BRANCH).strip(),
            remote=(merged.get("DSIGN_OTA_REMOTE", DEFAULT_REMOTE) or DEFAULT_REMOTE).strip(),
            enabled=_env_bool(merged.get("DSIGN_OTA_ENABLED"), default=True),
            display_backend=(merged.get("DSIGN_DISPLAY_BACKEND", "drm") or "drm").strip().lower(),
            dsign_user=(merged.get("DSIGN_USER", DSIGN_USER) or DSIGN_USER).strip(),
        )


def _env_bool(val: Optional[str], *, default: bool) -> bool:
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _state_path(cfg: OtaConfig) -> Path:
    return cfg.ota_dir / "state.json"


def _rollback_path(cfg: OtaConfig) -> Path:
    return cfg.ota_dir / "rollback.json"


def load_state(cfg: OtaConfig) -> Dict[str, Any]:
    path = _state_path(cfg)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(cfg: OtaConfig, state: Dict[str, Any]) -> None:
    cfg.ota_dir.mkdir(parents=True, exist_ok=True)
    _state_path(cfg).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_rollback(cfg: OtaConfig) -> Optional[Dict[str, Any]]:
    path = _rollback_path(cfg)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_rollback(cfg: OtaConfig, commit: str, branch: str) -> None:
    cfg.ota_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "previous_commit": commit,
        "branch": branch,
        "saved_at": _utc_now(),
    }
    _rollback_path(cfg).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    user: Optional[str] = None,
    timeout: float = 120.0,
    check: bool = False,
    run_fn: Optional[RunFn] = None,
) -> subprocess.CompletedProcess:
    runner = run_fn or subprocess.run
    full_cmd: List[str] = list(cmd)
    if user:
        full_cmd = ["sudo", "-n", "-u", user, *full_cmd]
    return runner(
        full_cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _git(cfg: OtaConfig, *args: str, run_fn: Optional[RunFn] = None, timeout: float = 180.0) -> subprocess.CompletedProcess:
    return _run(
        ["git", "-C", str(cfg.project_root), *args],
        user=cfg.dsign_user,
        timeout=timeout,
        run_fn=run_fn,
    )


def _rev_parse(cfg: OtaConfig, ref: str, run_fn: Optional[RunFn] = None) -> str:
    proc = _git(cfg, "rev-parse", ref, run_fn=run_fn, timeout=30.0)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"git rev-parse {ref} failed").strip())
    return (proc.stdout or "").strip()


def _ensure_git_repo(cfg: OtaConfig) -> None:
    if not (cfg.project_root / ".git").is_dir():
        raise RuntimeError(f"not a git repository: {cfg.project_root}")


def _working_tree_clean(cfg: OtaConfig, run_fn: Optional[RunFn] = None) -> bool:
    proc = _git(cfg, "status", "--porcelain", run_fn=run_fn, timeout=30.0)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "git status failed").strip())
    return not (proc.stdout or "").strip()


def check_update(cfg: OtaConfig, *, run_fn: Optional[RunFn] = None) -> Dict[str, Any]:
    _ensure_git_repo(cfg)
    fetch = _git(cfg, "fetch", cfg.remote, cfg.branch, run_fn=run_fn)
    if fetch.returncode != 0:
        raise RuntimeError((fetch.stderr or fetch.stdout or "git fetch failed").strip())

    local = _rev_parse(cfg, "HEAD", run_fn=run_fn)
    remote_ref = f"{cfg.remote}/{cfg.branch}"
    remote = _rev_parse(cfg, remote_ref, run_fn=run_fn)
    update_available = local != remote

    state = load_state(cfg)
    state.update(
        {
            "last_check_at": _utc_now(),
            "branch": cfg.branch,
            "remote": cfg.remote,
            "local_commit": local,
            "remote_commit": remote,
            "update_available": update_available,
        }
    )
    save_state(cfg, state)

    return {
        "success": True,
        "update_available": update_available,
        "local_commit": local,
        "remote_commit": remote,
        "branch": cfg.branch,
        "remote": cfg.remote,
    }


def download_update(cfg: OtaConfig, *, run_fn: Optional[RunFn] = None) -> Dict[str, Any]:
    info = check_update(cfg, run_fn=run_fn)
    if not info.get("update_available"):
        return {**info, "downloaded": False, "message": "already up to date"}

    if not _working_tree_clean(cfg, run_fn=run_fn):
        raise RuntimeError("working tree has local changes — commit or reset before OTA download")

    local = info["local_commit"]
    save_rollback(cfg, local, cfg.branch)

    merge = _git(
        cfg,
        "merge",
        "--ff-only",
        f"{cfg.remote}/{cfg.branch}",
        run_fn=run_fn,
        timeout=300.0,
    )
    if merge.returncode != 0:
        raise RuntimeError((merge.stderr or merge.stdout or "git merge --ff-only failed").strip())

    new_head = _rev_parse(cfg, "HEAD", run_fn=run_fn)
    state = load_state(cfg)
    state.update(
        {
            "downloaded_at": _utc_now(),
            "downloaded_commit": new_head,
            "update_available": False,
        }
    )
    save_state(cfg, state)

    return {
        "success": True,
        "downloaded": True,
        "local_commit": new_head,
        "previous_commit": local,
        "branch": cfg.branch,
    }


def _pip_install(cfg: OtaConfig, run_fn: Optional[RunFn] = None) -> None:
    pip = cfg.venv_dir / "bin" / "pip"
    if not pip.is_file():
        raise RuntimeError(f"pip not found: {pip}")

    req = cfg.project_root / "requirements.txt"
    setup = cfg.project_root / "setup.py"
    if req.is_file():
        cmd = [str(pip), "install", "-r", str(req)]
    elif setup.is_file():
        cmd = [str(pip), "install", "-e", str(cfg.project_root)]
    else:
        raise RuntimeError("neither requirements.txt nor setup.py found in project root")

    proc = _run(cmd, user=cfg.dsign_user, cwd=cfg.project_root, timeout=600.0, run_fn=run_fn)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "pip install failed").strip())


def _apply_manifest(cfg: OtaConfig, run_fn: Optional[RunFn] = None) -> None:
    apply_bin = os.environ.get("DSIGN_APPLY_INSTALL", "dsign-apply-install")
    env = os.environ.copy()
    env["DSIGN_PROJECT_ROOT"] = str(cfg.project_root)
    env["DSIGN_VENV"] = str(cfg.venv_dir)
    env["DSIGN_DISPLAY_BACKEND"] = cfg.display_backend
    proc = (run_fn or subprocess.run)(
        [apply_bin, "-q"],
        capture_output=True,
        text=True,
        timeout=300.0,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "dsign-apply-install failed").strip())


def _restart_units(cfg: OtaConfig, run_fn: Optional[RunFn] = None) -> List[str]:
    if cfg.display_backend == "wayland":
        units = [
            SIGNAGE_UNIT,
            "dsign-compositor.service",
            "dsign-logo.service",
            "dsign-mpv-wayland.service",
        ]
    else:
        units = [SIGNAGE_UNIT, "dsign-mpv.service"]

    restarted: List[str] = []
    for unit in units:
        proc = _run(["systemctl", "restart", unit], timeout=90.0, run_fn=run_fn)
        if proc.returncode == 0:
            restarted.append(unit)
    return restarted


def apply_update(cfg: OtaConfig, *, run_fn: Optional[RunFn] = None) -> Dict[str, Any]:
    _ensure_git_repo(cfg)
    commit = _rev_parse(cfg, "HEAD", run_fn=run_fn)

    _pip_install(cfg, run_fn=run_fn)
    _apply_manifest(cfg, run_fn=run_fn)
    restarted = _restart_units(cfg, run_fn=run_fn)

    state = load_state(cfg)
    state.update(
        {
            "last_apply_at": _utc_now(),
            "applied_commit": commit,
            "restarted_units": restarted,
        }
    )
    save_state(cfg, state)

    return {
        "success": True,
        "applied_commit": commit,
        "restarted_units": restarted,
    }


def rollback_update(cfg: OtaConfig, *, run_fn: Optional[RunFn] = None) -> Dict[str, Any]:
    rb = load_rollback(cfg)
    if not rb or not rb.get("previous_commit"):
        raise RuntimeError("no rollback point saved — run download/apply first")

    target = str(rb["previous_commit"])
    reset = _git(cfg, "reset", "--hard", target, run_fn=run_fn, timeout=60.0)
    if reset.returncode != 0:
        raise RuntimeError((reset.stderr or reset.stdout or "git reset --hard failed").strip())

    result = apply_update(cfg, run_fn=run_fn)
    state = load_state(cfg)
    state["last_rollback_at"] = _utc_now()
    state["rolled_back_to"] = target
    save_state(cfg, state)

    return {**result, "rolled_back_to": target}


def status_report(cfg: OtaConfig) -> Dict[str, Any]:
    state = load_state(cfg)
    rb = load_rollback(cfg)
    return {
        "success": True,
        "enabled": cfg.enabled,
        "project_root": str(cfg.project_root),
        "venv": str(cfg.venv_dir),
        "branch": cfg.branch,
        "remote": cfg.remote,
        "display_backend": cfg.display_backend,
        "state": state,
        "rollback": rb,
    }


def cmd_auto(cfg: OtaConfig, *, run_fn: Optional[RunFn] = None) -> Dict[str, Any]:
    if not cfg.enabled:
        return {"success": True, "skipped": True, "reason": "DSIGN_OTA_ENABLED=0"}

    info = check_update(cfg, run_fn=run_fn)
    if not info.get("update_available"):
        return {**info, "action": "none", "message": "up to date"}

    dl = download_update(cfg, run_fn=run_fn)
    applied = apply_update(cfg, run_fn=run_fn)
    return {
        "success": True,
        "action": "updated",
        "check": info,
        "download": dl,
        "apply": applied,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="DSign OTA self-update (D1)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("check", "download", "apply", "rollback", "status", "auto"):
        sub.add_parser(name, help=f"OTA {name}")

    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = OtaConfig.from_env()

    handlers = {
        "check": lambda: check_update(cfg),
        "download": lambda: download_update(cfg),
        "apply": lambda: apply_update(cfg),
        "rollback": lambda: rollback_update(cfg),
        "status": lambda: status_report(cfg),
        "auto": lambda: cmd_auto(cfg),
    }

    try:
        if args.command in ("apply", "rollback") and os.geteuid() != 0:
            raise PermissionError(f"sudo required for: dsign-update {args.command}")
        result = handlers[args.command]()
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            _print_human(args.command, result)
        if args.command == "check" and result.get("update_available"):
            return 1
        return 0
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"dsign-update: {exc}", file=sys.stderr)
        return 2


def _print_human(command: str, result: Dict[str, Any]) -> None:
    if command == "check":
        if result.get("update_available"):
            print(
                f"update available: {result.get('local_commit', '?')[:8]} "
                f"-> {result.get('remote_commit', '?')[:8]} ({result.get('branch')})"
            )
        else:
            print(f"up to date ({result.get('local_commit', '?')[:8]})")
    elif command == "download":
        if result.get("downloaded"):
            print(f"downloaded {result.get('local_commit', '?')[:8]}")
        else:
            print(result.get("message", "already up to date"))
    elif command == "apply":
        print(f"applied {result.get('applied_commit', '?')[:8]}")
        units = result.get("restarted_units") or []
        if units:
            print("restarted:", ", ".join(units))
    elif command == "rollback":
        print(f"rolled back to {result.get('rolled_back_to', '?')[:8]}")
    elif command == "status":
        st = result.get("state") or {}
        print(f"branch={result.get('branch')} enabled={result.get('enabled')}")
        if st.get("local_commit"):
            print(f"local={st['local_commit'][:8]} remote={str(st.get('remote_commit', ''))[:8]}")
        if st.get("last_apply_at"):
            print(f"last_apply={st['last_apply_at']}")
    elif command == "auto":
        print(result.get("message") or result.get("action", "done"))


if __name__ == "__main__":
    raise SystemExit(main())
