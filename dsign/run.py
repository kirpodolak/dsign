#!/usr/bin/env python3
"""
Launcher when ExecStart points here: .../<project>/dsign/run.py
PYTHONPATH root must be .../<project>/ (parent of this package directory).
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dsign.server import run_server

if __name__ == "__main__":
    run_server()
