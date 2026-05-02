#!/usr/bin/env python3
import sys
import os
import warnings

# engineio/async_drivers/eventlet emits EventletDeprecationWarning on import (journal spam).
warnings.filterwarnings("ignore", module=r"engineio\.async_drivers\.eventlet")

# Добавляем корень проекта в PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dsign.server import run_server

if __name__ == '__main__':
    run_server()