#!/usr/bin/env python3
"""
Entry point for Digital Signage.

Supports layouts:
  - run.py next to package dir: <root>/run.py + <root>/dsign/__init__.py
  - run.py inside package dir: <root>/dsign/run.py + <root>/dsign/__init__.py  → prepend <root>
"""
import sys
import os
from typing import Optional


def _has_nested_package(root: str) -> bool:
    return os.path.isfile(os.path.join(os.path.abspath(root), "dsign", "__init__.py"))


def _walk_parents_for_nested_package(*starts: str) -> Optional[str]:
    seen: set[str] = set()
    for start in starts:
        d = os.path.abspath(start)
        for _ in range(12):
            if d in seen:
                break
            seen.add(d)
            if _has_nested_package(d):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


_here = os.path.dirname(os.path.abspath(__file__))
_cwd = os.getcwd()

_roots_to_try: list[str] = []

# run.py sits inside .../dsign/ next to __init__.py → import root is parent directory
if os.path.isfile(os.path.join(_here, "__init__.py")):
    parent = os.path.dirname(_here)
    if parent and parent != _here:
        _roots_to_try.append(parent)

_env = (os.environ.get("DSIGN_PROJECT_ROOT") or "").strip()
if _env:
    _roots_to_try.append(os.path.abspath(_env))

_roots_to_try.extend([_here, _cwd])

chosen = None
for candidate in _roots_to_try:
    if candidate and _has_nested_package(candidate):
        chosen = os.path.abspath(candidate)
        break

if not chosen:
    chosen = _walk_parents_for_nested_package(_here, _cwd)

if chosen:
    if chosen not in sys.path:
        sys.path.insert(0, chosen)
else:
    if _here not in sys.path:
        sys.path.insert(0, _here)

from dsign.server import run_server

if __name__ == "__main__":
    run_server()
