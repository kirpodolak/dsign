#!/usr/bin/env python3
import sys
import os


def _project_root_containing_dsign_package(start_dir: str) -> str:
    """
    Ensure `import dsign` works whether run.py lives at repo root or one level deeper
    (common mistaken copy: /home/dsign/dsign/run.py vs /home/dsign/run.py).
    """
    d = os.path.abspath(start_dir)
    for _ in range(6):
        pkg_init = os.path.join(d, "dsign", "__init__.py")
        if os.path.isfile(pkg_init):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.abspath(start_dir)


_here = os.path.dirname(os.path.abspath(__file__))
_root = _project_root_containing_dsign_package(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)

from dsign.server import run_server

if __name__ == "__main__":
    run_server()
