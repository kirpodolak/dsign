#!/usr/bin/env python3
import sys
import os
from typing import Optional


def _has_dsign_package(d: str) -> bool:
    return os.path.isfile(os.path.join(os.path.abspath(d), "dsign", "__init__.py"))


def _project_root_containing_dsign_package(*candidate_dirs: str) -> Optional[str]:
    """
    Walk parents of each candidate until a directory containing `dsign/__init__.py` is found.
    """
    seen: set[str] = set()
    for start in candidate_dirs:
        d = os.path.abspath(start)
        for _ in range(10):
            if d in seen:
                break
            seen.add(d)
            if _has_dsign_package(d):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


# Explicit layout override (run.py may live in scripts/, tools/, etc.)
_env_root = (os.environ.get("DSIGN_PROJECT_ROOT") or "").strip()
_here = os.path.dirname(os.path.abspath(__file__))
_cwd = os.getcwd()

_root = None
if _env_root and _has_dsign_package(_env_root):
    _root = os.path.abspath(_env_root)
else:
    _root = _project_root_containing_dsign_package(_here, _cwd)

if _root:
    if _root not in sys.path:
        sys.path.insert(0, _root)
else:
    # Last resort: directory of run.py only (may fail if run.py is isolated)
    if _here not in sys.path:
        sys.path.insert(0, _here)

from dsign.server import run_server

if __name__ == "__main__":
    run_server()
