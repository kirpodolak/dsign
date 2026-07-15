#!/usr/bin/env bash
# Deploy Stop / return-to-schedule / pool-leak fixes onto the Pi prod runtime.
#
# Why this exists:
#   systemd runs /home/dsign/dsign (flat prod tree).
#   git/OTA often lives in /home/dsign/dsign-new.
#   Updating only the clone leaves signage on stale code — Stop / return look dead.
#
# Usage (on the player, as a user with sudo):
#   curl -fsSL https://raw.githubusercontent.com/kirpodolak/dsign/cursor/sqlalchemy-pool-leak-8ed1/scripts/hotfix-playback-controls-pi.sh \
#     | sudo bash
#
# Optional env:
#   BRANCH=cursor/sqlalchemy-pool-leak-8ed1
#   RUNTIME_ROOT=/home/dsign/dsign
#   PROJECT_ROOT=/home/dsign/dsign-new   # git clone; optional
#   SKIP_RESTART=1

set -euo pipefail

BRANCH="${BRANCH:-cursor/sqlalchemy-pool-leak-8ed1}"
REPO="${REPO:-kirpodolak/dsign}"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/home/dsign/dsign}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/dsign/dsign-new}"
DSIGN_USER="${DSIGN_USER:-dsign}"
SIGNAGE_UNIT="${SIGNAGE_UNIT:-digital-signage.service}"

die() { echo "ERROR: $*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

need_cmd curl
need_cmd sudo
need_cmd systemctl
need_cmd grep

if [[ "$(id -u)" -ne 0 ]]; then
  die "run as root (sudo bash …)"
fi

wd="$(systemctl show -p WorkingDirectory --value "$SIGNAGE_UNIT" 2>/dev/null || true)"
exec_start="$(systemctl show -p ExecStart --value "$SIGNAGE_UNIT" 2>/dev/null || true)"
echo "==> systemd $SIGNAGE_UNIT"
echo "    WorkingDirectory=${wd:-?}"
echo "    ExecStart=${exec_start:-?}"

if [[ -n "$wd" && "$wd" != "$RUNTIME_ROOT" ]]; then
  echo "WARN: WorkingDirectory ($wd) != RUNTIME_ROOT ($RUNTIME_ROOT); using WorkingDirectory"
  RUNTIME_ROOT="$wd"
fi

[[ -d "$RUNTIME_ROOT" ]] || die "runtime root missing: $RUNTIME_ROOT"

# Flat prod layout: services/, routes/, static/, extensions.py at RUNTIME_ROOT.
# Nested clone layout: dsign/services/ under PROJECT_ROOT.
FILES=(
  "extensions.py"
  "config/config.py"
  "services/schedule_engine.py"
  "services/schedule_service.py"
  "services/playback_service.py"
  "services/playlist_management.py"
  "services/playback_play.py"
  "services/playback_network.py"
  "services/playback_slideshow.py"
  "services/logo_management.py"
  "routes/api/api_routes.py"
  "static/js/index.js"
)

install_one() {
  local rel="$1"
  local dest_root="$2"
  local url="${RAW}/dsign/${rel}"
  local dest="${dest_root}/${rel}"
  mkdir -p "$(dirname "$dest")"
  echo "  curl → ${dest}"
  curl -fsSL "$url" -o "$dest"
}

echo "==> installing fix files into RUNTIME_ROOT=$RUNTIME_ROOT"
for rel in "${FILES[@]}"; do
  install_one "$rel" "$RUNTIME_ROOT"
done

if [[ -d "$PROJECT_ROOT/dsign/services" ]]; then
  echo "==> also updating git clone PROJECT_ROOT=$PROJECT_ROOT (keeps trees aligned)"
  for rel in "${FILES[@]}"; do
    install_one "$rel" "$PROJECT_ROOT/dsign"
  done
elif [[ -d "$PROJECT_ROOT/services" ]]; then
  echo "==> also updating flat PROJECT_ROOT=$PROJECT_ROOT"
  for rel in "${FILES[@]}"; do
    install_one "$rel" "$PROJECT_ROOT"
  done
else
  echo "NOTE: PROJECT_ROOT=$PROJECT_ROOT has no services/; skipped clone update"
fi

echo "==> chown ${DSIGN_USER}:${DSIGN_USER}"
chown -R "${DSIGN_USER}:${DSIGN_USER}" "$RUNTIME_ROOT" || true
if [[ -d "$PROJECT_ROOT" ]]; then
  chown -R "${DSIGN_USER}:${DSIGN_USER}" "$PROJECT_ROOT" || true
fi

echo "==> verifying markers in RUNTIME_ROOT"
fail=0
grep -q "NullPool" "$RUNTIME_ROOT/extensions.py" || { echo "MISSING NullPool in extensions.py"; fail=1; }
grep -q "_control_lock" "$RUNTIME_ROOT/services/playlist_management.py" || { echo "MISSING _control_lock"; fail=1; }
grep -q "def enqueue_stop" "$RUNTIME_ROOT/services/playback_service.py" || { echo "MISSING enqueue_stop"; fail=1; }
grep -q "def _halt_mpv_playback" "$RUNTIME_ROOT/services/playlist_management.py" || { echo "MISSING _halt_mpv_playback"; fail=1; }
grep -q "stale_playing" "$RUNTIME_ROOT/services/playlist_management.py" || { echo "MISSING stale_playing"; fail=1; }
grep -q "mark_play_starting" "$RUNTIME_ROOT/services/playlist_management.py" || { echo "MISSING mark_play_starting"; fail=1; }
grep -q "claim_playback_intent" "$RUNTIME_ROOT/services/playlist_management.py" || { echo "MISSING claim_playback_intent"; fail=1; }
grep -q "def _prepare_mpv_for_new_play" "$RUNTIME_ROOT/services/playlist_management.py" || { echo "MISSING _prepare_mpv_for_new_play"; fail=1; }
grep -q "status == \"playing\"" "$RUNTIME_ROOT/services/schedule_engine.py" || { echo "MISSING status==playing in schedule plan"; fail=1; }
grep -q "stale_playing" "$RUNTIME_ROOT/static/js/index.js" || { echo "MISSING stale_playing in index.js"; fail=1; }
grep -q "orphan_mpv" "$RUNTIME_ROOT/static/js/index.js" || { echo "MISSING orphan_mpv in index.js"; fail=1; }
grep -q "return-to-schedule" "$RUNTIME_ROOT/routes/api/api_routes.py" || { echo "MISSING return-to-schedule route"; fail=1; }
[[ "$fail" -eq 0 ]] || die "marker check failed — wrong tree or fetch failed"

find "$RUNTIME_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

if [[ "${SKIP_RESTART:-0}" == "1" ]]; then
  echo "==> SKIP_RESTART=1 — not restarting"
else
  echo "==> systemctl restart $SIGNAGE_UNIT"
  systemctl restart "$SIGNAGE_UNIT"
  sleep 2
  systemctl is-active "$SIGNAGE_UNIT" || die "$SIGNAGE_UNIT not active"
fi

echo
echo "OK: prod runtime has pool/playback control hotfixes."
echo "Hard-reload the browser (Ctrl+Shift+R), then try Stop / Return to schedule."
echo "If still stuck:"
echo "  journalctl -u $SIGNAGE_UNIT -n 80 --no-pager"
echo "  grep -E '_control_lock|enqueue_stop|NullPool' $RUNTIME_ROOT/services/playlist_management.py $RUNTIME_ROOT/extensions.py | head"
