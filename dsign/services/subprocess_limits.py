"""Default subprocess timeouts for external commands (backlog H-SUB).

All ``subprocess.run`` / ``subprocess.check_output`` in ``dsign/`` must pass
``timeout=``. Long-running workers may use ``Popen`` with an explicit deadline
loop — see ``tests/test_subprocess_audit.py`` allowlist.
"""

from __future__ import annotations

AMIXER_TIMEOUT_SEC = 3.0
APLAY_LIST_TIMEOUT_SEC = 5.0
IP_ADDR_TIMEOUT_SEC = 3.0
DISPLAY_APPLY_TIMEOUT_SEC = 90.0
DEP_CHECK_TIMEOUT_SEC = 5.0
FFPROBE_TIMEOUT_SEC = 15.0
SYSTEMCTL_QUERY_TIMEOUT_SEC = 5.0
NMCLI_DEFAULT_TIMEOUT_SEC = 20.0
