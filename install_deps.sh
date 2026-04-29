#!/usr/bin/env bash
set -euo pipefail

# Install system + Python dependencies for the dsign project.
#
# This script is intentionally side-effect-light:
# - It does NOT create users
# - It does NOT set up systemd services
# - It does NOT clone/pull the repository
#
# It only installs:
# - OS packages (mpv/ffmpeg/yt-dlp/nginx/etc.)
# - Python virtualenv + pip requirements
#
# Usage:
#   sudo ./scripts/install_deps.sh
#
# Optional env:
#   DSIGN_VENV_DIR=/opt/dsign/venv
#   DSIGN_PYTHON=python3

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root (use sudo)." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ_FILE="${ROOT_DIR}/requirements.txt"

if [ ! -f "$REQ_FILE" ]; then
  echo "requirements.txt not found at: $REQ_FILE" >&2
  exit 1
fi

DSIGN_PYTHON="${DSIGN_PYTHON:-python3}"
DSIGN_VENV_DIR="${DSIGN_VENV_DIR:-${ROOT_DIR}/.venv}"

install_apt() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update

  # Network helper tools are optional at runtime (only used if you enable the tty onboarding).
  # Keep them in deps by default: they are small and widely available.
  apt-get install -y \
    ca-certificates curl git \
    python3 python3-venv python3-pip python3-dev \
    build-essential \
    sqlite3 libsqlite3-dev \
    mpv ffmpeg yt-dlp \
    socat \
    nginx \
    acl \
    libdrm-dev \
    network-manager \
    alsa-utils
}

install_dnf() {
  dnf -y install \
    ca-certificates curl git \
    python3 python3-devel python3-pip \
    gcc gcc-c++ make \
    sqlite sqlite-devel \
    mpv ffmpeg yt-dlp \
    socat \
    nginx \
    acl \
    libdrm libdrm-devel \
    NetworkManager
}

install_pacman() {
  pacman -Sy --noconfirm \
    ca-certificates curl git \
    python python-pip \
    base-devel \
    sqlite \
    mpv ffmpeg yt-dlp \
    socat \
    nginx \
    acl \
    libdrm \
    networkmanager
}

if command -v apt-get >/dev/null 2>&1; then
  install_apt
elif command -v dnf >/dev/null 2>&1; then
  install_dnf
elif command -v pacman >/dev/null 2>&1; then
  install_pacman
else
  echo "Unsupported distro: no apt-get/dnf/pacman found." >&2
  exit 2
fi

if ! command -v "$DSIGN_PYTHON" >/dev/null 2>&1; then
  echo "Python executable not found: $DSIGN_PYTHON" >&2
  exit 3
fi

echo "Creating venv at: $DSIGN_VENV_DIR"
"$DSIGN_PYTHON" -m venv "$DSIGN_VENV_DIR"

echo "Installing Python deps from: $REQ_FILE"
"$DSIGN_VENV_DIR/bin/pip" install --upgrade pip wheel
"$DSIGN_VENV_DIR/bin/pip" install -r "$REQ_FILE"

echo ""
echo "Done."
echo "Venv: $DSIGN_VENV_DIR"
echo "Next (manual): run the app with:"
echo "  $DSIGN_VENV_DIR/bin/python ${ROOT_DIR}/run.py"
