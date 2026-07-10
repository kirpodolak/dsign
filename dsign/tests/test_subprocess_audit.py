"""Static audit: subprocess.run/check_output in app code must pass timeout= (H-SUB)."""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

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

# routes/ + services/ on main; fail fast if layout discovery breaks on Pi.
MIN_EXPECTED_APP_PY_FILES = 30


@functools.lru_cache(maxsize=1)
def _pkg_root() -> Path:
    """
    Locate the dsign package root (directory containing routes/ and services/).

    Supports CI (repo/dsign/tests), Pi prod (~/dsign/dsign/tests), and nested
    ~/dsign/dsign/dsign/ copies by walking up from this test module.
    """
    tests_dir = Path(__file__).resolve().parent
    candidates: list[Path] = []
    for base in [tests_dir, *tests_dir.parents[:6]]:
        candidates.append(base)
        nested = base / "dsign"
        if nested != base:
            candidates.append(nested)

    seen: set[Path] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        if (cand / "routes").is_dir() and (cand / "services").is_dir():
            return cand

    tried = ", ".join(str(c) for c in candidates[:12])
    pytest.fail(
        "could not locate dsign package root (need routes/ + services/). "
        f"Searched from {tests_dir}; candidates included: {tried}"
    )


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def _rel_pkg_path(path: Path) -> str:
    return path.relative_to(_pkg_root()).as_posix()


def _iter_prod_py_files() -> list[Path]:
    root = _pkg_root()
    files: list[Path] = []
    for sub in SCAN_SUBDIRS:
        scan_root = root / sub
        if not scan_root.is_dir():
            continue
        for path in sorted(scan_root.rglob("*.py")):
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
    files = _iter_prod_py_files()
    assert len(files) >= MIN_EXPECTED_APP_PY_FILES, (
        f"audit scanned too few files ({len(files)}) under {_pkg_root()} — "
        "check Pi layout (routes/ + services/)"
    )

    violations: list[str] = []
    for path in files:
        missing = _calls_missing_timeout(path)
        if missing:
            violations.append(f"{_rel_pkg_path(path)}: {missing}")
    assert not violations, "subprocess calls without timeout=:\n" + "\n".join(violations)


def test_subprocess_popen_is_allowlisted_or_absent():
    """Popen is only allowed on the manual-deadline allowlist."""
    violations: list[str] = []
    for path in _iter_prod_py_files():
        popens = _popen_lines_without_allowlist(path)
        if popens:
            violations.append(f"{_rel_pkg_path(path)}: Popen at lines {popens}")
    assert not violations, (
        "unexpected subprocess.Popen (add deadline or allowlist suffix):\n"
        + "\n".join(violations)
    )


def test_subprocess_audit_scans_only_app_trees():
    """Guard: audit must not pick up venv / site-packages on player workstations."""
    scanned = {_rel_pkg_path(p) for p in _iter_prod_py_files()}
    assert len(scanned) >= MIN_EXPECTED_APP_PY_FILES, (
        f"expected >= {MIN_EXPECTED_APP_PY_FILES} app .py files, got {len(scanned)} "
        f"(pkg root {_pkg_root()})"
    )
    for rel in scanned:
        parts = rel.split("/")
        assert "site-packages" not in parts
        assert "dsign-new" not in parts
        assert ".venv" not in parts
        assert "venv" not in parts
