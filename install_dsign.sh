#!/bin/bash

# Проверка на root
if [ "$(id -u)" -ne 0 ]; then
  echo "Этот скрипт должен запускаться с правами root" >&2
  exit 1
fi

set -euo pipefail

# Конфигурация
DSIGN_USER="dsign"
WWW_USER="www-data"
PROJECT_DIR="/home/dsign/dsign"
VENV_DIR="/home/dsign/venv"
CONFIG_DIR="/home/dsign/config"
LOG_DIR="/var/log/dsign"
DB_DIR="/var/lib/dsign"
DB_FILE="$DB_DIR/database.db"
UPLOAD_DIR="/var/lib/dsign/media"
SETTINGS_FILE="$DB_DIR/settings.json"
X11_USER_DIR="/home/dsign/.X11"
SYSTEMD_DIR="/etc/systemd/system"
UDEV_RULES_DIR="/etc/udev/rules.d"
BIN_DIR="/usr/local/bin"

# Installer toggles (headless-friendly defaults)
# - DSIGN_SETUP_X11=1 enables xauth setup (only useful if you run a real X session on :0)
# - DSIGN_GIT_REF can pin a branch/tag/commit (default: main)
DSIGN_SETUP_X11="${DSIGN_SETUP_X11:-0}"
DSIGN_GIT_REF="${DSIGN_GIT_REF:-main}"

# Создание пользователя и групп
if ! id "$DSIGN_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$DSIGN_USER"
fi
usermod -a -G "$DSIGN_USER" "$WWW_USER"
usermod -a -G "$WWW_USER" "$DSIGN_USER"
usermod -a -G video "$DSIGN_USER"  # Для доступа к /dev/dri

# Установка зависимостей
apt-get update
apt-get install -y \
    python3-pip python3-venv python3-dev \
    sqlite3 libsqlite3-dev \
    mpv ffmpeg yt-dlp \
    alsa-utils \
    socat \
    nginx git \
    acl libdrm-dev \
    xauth  # optional (only if DSIGN_SETUP_X11=1)

# Создание структуры директорий
mkdir -p "$PROJECT_DIR" "$VENV_DIR" "$CONFIG_DIR" "$LOG_DIR" "$UPLOAD_DIR" "$DB_DIR" "$X11_USER_DIR"
chown -R "$DSIGN_USER:$DSIGN_USER" "/home/dsign" "$DB_DIR" "$LOG_DIR" "$X11_USER_DIR"
chmod -R 775 "$UPLOAD_DIR" "$DB_DIR"

# Directories required by services/scripts
mkdir -p "$DB_DIR/mpv" "$DB_DIR/mpv-minimal" "$UPLOAD_DIR/tmp"
chown -R "$DSIGN_USER:video" "$DB_DIR/mpv" "$DB_DIR/mpv-minimal" || true
chmod 775 "$DB_DIR/mpv" "$DB_DIR/mpv-minimal" || true
chown -R "$DSIGN_USER:$DSIGN_USER" "$UPLOAD_DIR/tmp" || true
chmod 775 "$UPLOAD_DIR/tmp" || true

# Настройка X11 авторизации (не требуется для DRM/KMS headless)
if [ "$DSIGN_SETUP_X11" = "1" ]; then
  sudo -u "$DSIGN_USER" xauth generate :0 . trusted || true
  chown "$DSIGN_USER:$DSIGN_USER" "$X11_USER_DIR/Xauthority" || true
  ln -sf "$X11_USER_DIR/Xauthority" "/home/dsign/.Xauthority" || true
fi

# Настройка ACL
setfacl -R -m u:"$WWW_USER":rwX "$PROJECT_DIR" "$UPLOAD_DIR" "$DB_DIR"
setfacl -Rd -m u:"$WWW_USER":rwX "$PROJECT_DIR" "$UPLOAD_DIR" "$DB_DIR"

# Preview capture writes new temp files under static/images; must be owned by dsign (not root).
mkdir -p "$PROJECT_DIR/static/images"
chown -R "$DSIGN_USER:$DSIGN_USER" "$PROJECT_DIR/static/images"
chmod 775 "$PROJECT_DIR/static/images"

# Получение репозитория (идемпотентно)
if [ -d "$PROJECT_DIR/.git" ]; then
  sudo -u "$DSIGN_USER" git -C "$PROJECT_DIR" fetch --all --prune
  sudo -u "$DSIGN_USER" git -C "$PROJECT_DIR" checkout -q "$DSIGN_GIT_REF" || true
  sudo -u "$DSIGN_USER" git -C "$PROJECT_DIR" pull --ff-only || true
else
  rm -rf "$PROJECT_DIR"
  sudo -u "$DSIGN_USER" git clone https://github.com/kirpodolak/dsign.git "$PROJECT_DIR"
  sudo -u "$DSIGN_USER" git -C "$PROJECT_DIR" checkout -q "$DSIGN_GIT_REF" || true
fi

# Создание файлов БД и настроек
touch "$DB_FILE" "$SETTINGS_FILE" "$LOG_DIR/dsign.log" "$LOG_DIR/app.log"
chown "$DSIGN_USER:$DSIGN_USER" "$DB_FILE" "$SETTINGS_FILE" "$LOG_DIR/dsign.log" "$LOG_DIR/app.log"
chmod 664 "$DB_FILE" "$SETTINGS_FILE"

# Настройка виртуального окружения
sudo -u "$DSIGN_USER" python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip wheel
pip install -r "$PROJECT_DIR/requirements.txt"
deactivate

# Конфигурационный файл
cat > "$CONFIG_DIR/config.py" <<EOL
import os

class Config:
    SECRET_KEY = '$(openssl rand -hex 32)'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///$DB_FILE'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = '$UPLOAD_DIR'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    LOG_DIR = '$LOG_DIR'
    # Keep this in sync with systemd `dsign-mpv.service --input-ipc-server=...`
    MPV_SOCKET = '/var/lib/dsign/mpv/socket'
EOL

# Инициализация базы данных
cat > "$PROJECT_DIR/init_db.py" <<EOL
import os

from dsign import create_app
from dsign.extensions import db
from dsign.models import User

# Point app to the generated config.py
os.environ["DSIGN_CONFIG"] = "$CONFIG_DIR/config.py"

app = create_app()

with app.app_context():
    db.create_all()
    admin = db.session.query(User).filter_by(username="admin").first()
    if not admin:
        admin = User(username="admin", is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("Создан администратор: admin/admin123")
EOL

# Выполнение инициализации БД
sudo -u "$DSIGN_USER" "$VENV_DIR/bin/python" "$PROJECT_DIR/init_db.py"
rm "$PROJECT_DIR/init_db.py"

# ======================
# Install scripts + systemd units from repo (single source of truth)
# ======================

# Install all scripts shipped under usr/local/bin/
mkdir -p "$BIN_DIR"
for f in "$PROJECT_DIR/usr/local/bin/"*; do
  [ -f "$f" ] || continue
  bn="$(basename "$f")"
  install -m 0755 "$f" "$BIN_DIR/$bn"
  sed -i 's/\r$//' "$BIN_DIR/$bn" || true
done
chown root:root "$BIN_DIR"/dsign-* 2>/dev/null || true

# Install systemd units/timers from etc/systemd/system/
mkdir -p "$SYSTEMD_DIR"
for u in "$PROJECT_DIR/etc/systemd/system/"*.service "$PROJECT_DIR/etc/systemd/system/"*.timer; do
  [ -f "$u" ] || continue
  install -m 0644 "$u" "$SYSTEMD_DIR/$(basename "$u")"
done

# MPV minimal config under /var/lib/dsign (owned by dsign — editable without root)
if [ -f "$PROJECT_DIR/etc/dsign/mpv-minimal/mpv.conf" ]; then
  install -m 0644 "$PROJECT_DIR/etc/dsign/mpv-minimal/mpv.conf" "$DB_DIR/mpv-minimal/mpv.conf"
  chown -R "$DSIGN_USER:video" "$DB_DIR/mpv-minimal" || true
  chmod 775 "$DB_DIR/mpv-minimal" || true
  chmod 664 "$DB_DIR/mpv-minimal/mpv.conf" 2>/dev/null || true
fi

# Allow the web UI to start the screenshot service without a password prompt.
# Keep it narrow: only `systemctl start screenshot.service`.
# NOPASSWD alone allows non-interactive sudo (no requiretty; older sudo rejects !requiretty).
cat > /etc/sudoers.d/dsign-screenshot <<'EOL'
www-data ALL=(root) NOPASSWD: /bin/systemctl start screenshot.service
www-data ALL=(root) NOPASSWD: /usr/bin/systemctl start screenshot.service
dsign ALL=(root) NOPASSWD: /bin/systemctl start screenshot.service
dsign ALL=(root) NOPASSWD: /usr/bin/systemctl start screenshot.service
# Allow the app (user dsign) to recover MPV when IPC gets stuck.
# Keep it narrow: only restart dsign-mpv.service.
dsign ALL=(root) NOPASSWD: /bin/systemctl restart dsign-mpv.service
dsign ALL=(root) NOPASSWD: /usr/bin/systemctl restart dsign-mpv.service
EOL
chmod 440 /etc/sudoers.d/dsign-screenshot

# Настройка прав на DRI устройства
mkdir -p "$UDEV_RULES_DIR"
cat > "$UDEV_RULES_DIR/99-dsign.rules" <<EOL
KERNEL=="card0", GROUP="video", MODE="0660"
KERNEL=="renderD128", GROUP="video", MODE="0660"
EOL

udevadm control --reload-rules
udevadm trigger

systemctl daemon-reload

# Enable core services (best-effort: not all boxes have tty1/drm available during install)
systemctl enable digital-signage.service dsign-mpv.service dsign-network-assistant.service || true
systemctl enable screenshot.timer || true
systemctl start dsign-network-assistant.service dsign-mpv.service digital-signage.service || true

# Настройка Nginx
cat > /etc/nginx/sites-available/dsign <<EOL
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location /static/ {
        alias $PROJECT_DIR/static/;
        expires 30d;
    }

    location /uploads/ {
        alias $UPLOAD_DIR/;
        expires 30d;
    }
}
EOL

ln -sf /etc/nginx/sites-available/dsign /etc/nginx/sites-enabled/dsign
systemctl restart nginx

echo "Установка завершена!"
echo "----------------------------------------"
echo "База данных: $DB_FILE"
echo "Администратор: admin/admin123"
echo "Сервисы:"
echo "  Веб-интерфейс: systemctl status digital-signage.service"
echo "  MPV плеер:     systemctl status dsign-mpv.service"
echo "----------------------------------------"
