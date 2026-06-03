# PR #64 — reconciled with origin/main (2026-06-03)
# Branch: cursor/fix-stream-mpv-transition-44fd (intent) on top of current main

| File | Lines (export) | Lines (main before patch) |
|------|----------------|---------------------------|
| README.md | 175 | 162 |
| dsign/services/logo_management.py | 274 | 240 |
| dsign/services/mpv_management.py | 885 | 885 |
| dsign/services/playlist_management.py | 1852 | 1848 |
| etc/systemd/system/dsign-mpv.service | 54 | 56 |
| install_dsign.sh | 290 | 289 |
| usr/local/bin/dsign-mpv-post-start | 4 | 134 |
| usr/local/bin/dsign-show-startup-ip | 138 | 134 |

Do NOT copy mpv_management.py from the old PR branch (690 lines) — it drops persistent IPC.
Use pr64-export/dsign/services/mpv_management.py (885 lines).
