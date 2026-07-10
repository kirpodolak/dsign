"""Static audit: subprocess.run/check_output in app code must pass timeout= (H-SUB)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Package root: .../dsign/ (parent of tests/)
DSIGN_PKG_ROOT = Path(__file__).resolve().parents[1]

# Only scan first-party app trees (not venv, dsign-new, site-packages, tests).
SCAN_SUBDIRS = ("routes", "services")

SKIP_DIR_NAMES = frozenset(
    {
        "tests",
        "__pycache__",
        ".venv",
        "venv",
        "site-packages",
        "dsign-new",
        "node_modules",
        ".git",
    }
)

# Popen with manual deadline — suffix paths inside the dsign package.
POPEN_ALLOWLIST_SUFFIXES = frozenset(
    {
        "services/content_cache.py",  # yt-dlp prefetch loop
        "services/file_service.py",  # ffmpeg transcode progress stream
        "routes/api/api_routes.py",  # dsign-wifi-on-display daemon
    }
)


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def _rel_pkg_path(path: Path) -> str:
    return path.relative_to(DSIGN_PKG_ROOT).as_posix()


def _iter_prod_py_files() -> list[Path]:
    files: list[Path] = []
    for sub in SCAN_SUBDIRS:
        root = DSIGN_PKG_ROOT / sub
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if _is_skipped(path):
                continue
            files.append(path)
    return files


def _calls_missing_timeout(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"syntax error in {_rel_pkg_path(path)}: {exc}")

    missing: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in ("run", "check_output")
        ):
            continue
        if not any(kw.arg == "timeout" for kw in node.keywords):
            missing.append((node.lineno, func.attr))
    return missing


def _popen_lines_without_allowlist(path: Path) -> list[int]:
    rel = _rel_pkg_path(path)
    if rel in POPEN_ALLOWLIST_SUFFIXES:
        return []

    src = path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src, filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr == "Popen"
        ):
            lines.append(node.lineno)
    return lines


def test_subprocess_run_and_check_output_have_timeout():
    """Every subprocess.run/check_output in routes/ + services/ must set timeout=."""
    violations: list[str] = []
    for path in _iter_prod_py_files():
        missing = _calls_missing_timeout(path)
        if missing:
            rel = _rel_pkg_path(path)
            violations.append(f"{rel}: {missing}")
    assert not violations, "subprocess calls without timeout=:\n" + "\n".join(violations)


def test_subprocess_popen_is_allowlisted_or_absent():
    """Popen is only allowed on the manual-deadline allowlist."""
    violations: list[str] = []
    for path in _iter_prod_py_files():
        popens = _popen_lines_without_allowlist(path)
        if popens:
            rel = _rel_pkg_path(path)
            violations.append(f"{rel}: Popen at lines {popens}")
    assert not violations, (
        "unexpected subprocess.Popen (add deadline or allowlist suffix):\n"
        + "\n".join(violations)
    )


def test_subprocess_audit_scans_only_app_trees():
    """Guard: audit must not pick up venv / site-packages on player workstations."""
    scanned = {_rel_pkg_path(p) for p in _iter_prod_py_files()}
    assert scanned, "expected at least one app .py file under routes/ or services/"
    for rel in scanned:
        parts = rel.split("/")
        assert "site-packages" not in parts
        assert "dsign-new" not in parts
        assert ".venv" not in parts
        assert "venv" not in parts