"""Static audit: every subprocess.run/check_output in prod code must pass timeout= (H-SUB)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DSIGN_ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = frozenset({"tests", "__pycache__"})

# Long-running workers use Popen + manual deadline (not subprocess.run timeout).
POPEN_ALLOWLIST = frozenset(
    {
        "dsign/services/content_cache.py",  # yt-dlp prefetch loop
        "dsign/services/file_service.py",  # ffmpeg transcode progress stream
        "dsign/routes/api/api_routes.py",  # dsign-wifi-on-display daemon
    }
)


def _iter_prod_py_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(DSIGN_ROOT.rglob("*.py")):
        rel = path.relative_to(DSIGN_ROOT.parent)
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def _calls_missing_timeout(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"syntax error in {path}: {exc}")

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


def _popen_without_allowlist(path: Path) -> list[int]:
    rel = str(path.relative_to(DSIGN_ROOT.parent)).replace("\\", "/")
    if rel in POPEN_ALLOWLIST:
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


@pytest.mark.parametrize("path", _iter_prod_py_files(), ids=lambda p: p.relative_to(DSIGN_ROOT.parent).as_posix())
def test_subprocess_run_and_check_output_have_timeout(path: Path):
    missing = _calls_missing_timeout(path)
    assert not missing, (
        f"{path.relative_to(DSIGN_ROOT.parent)}: subprocess calls without timeout=: {missing}"
    )


@pytest.mark.parametrize("path", _iter_prod_py_files(), ids=lambda p: p.relative_to(DSIGN_ROOT.parent).as_posix())
def test_subprocess_popen_is_allowlisted_or_absent(path: Path):
    popens = _popen_without_allowlist(path)
    assert not popens, (
        f"{path.relative_to(DSIGN_ROOT.parent)}: subprocess.Popen at lines {popens} "
        f"— add manual deadline or extend POPEN_ALLOWLIST with justification"
    )
