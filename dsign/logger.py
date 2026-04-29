"""
Backward-compatible logger shim.

Some deployments import `dsign.logger` (historical path). The canonical logger
module lives at `dsign/services/logger.py`, but importing `dsign.services.logger`
requires importing the `dsign.services` package first, which may execute an
older `dsign/services/__init__.py` with heavy/circular imports in some installs.

To keep startup robust, we load `services/logger.py` by file path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast


def _load_services_logger() -> ModuleType:
    here = Path(__file__).resolve()
    target = here.parent / "services" / "logger.py"
    spec = importlib.util.spec_from_file_location("dsign_services_logger", str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load services logger from {target}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_services_logger()

ServiceLogger = cast(Any, getattr(_mod, "ServiceLogger"))
setup_logger = cast(Any, getattr(_mod, "setup_logger"))
setup_flask_logging = cast(Any, getattr(_mod, "setup_flask_logging"))

__all__ = ["ServiceLogger", "setup_logger", "setup_flask_logging"]
