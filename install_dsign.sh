#!/bin/bash

# Проверка на root
if [ "$(id -u)" -ne 0 ]; then
  echo "Этот скрипт должен запускаться с правами root" >&2
  exit 1
fi

# Конфигурация
DSIGN_USER="dsign"
WWW_USER="www-data"
# drm (default) | wayland — Wayland uses labwc + imv + dsign-mpv-wayland.service
DSIGN_DISPLAY_BACKEND="${DSIGN_DISPLAY_BACKEND:-drm}"
PROJECT_DIR="/home/dsign/dsign"
VENV_DIR="/home/dsign/venv"
CONFIG_DIR="/home/dsign/config"
LOG_DIR="/var/log/dsign"
DB_DIR="/var/lib/dsign"
DB_FILE="$DB_DIR/database.db"
UPLOAD_DIR="/var/lib/dsign/media"
SETTINGS_FILE="$DB_DIR/settings.json"
X11_USER_DIR="/home/dsign/.X11"

# Создание пользователя и групп
if ! id "$DSIGN_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$DSIGN_USER"
fi
usermod -a -G "$DSIGN_USER" "$WWW_USER"
usermod -a -G "$WWW_USER" "$DSIGN_USER"
usermod -a -G video "$DSIGN_USER"  # Для доступа к /dev/dri
usermod -a -G render "$DSIGN_USER" 2>/dev/null || true
usermod -a -G input "$DSIGN_USER" 2>/dev/null || true  # seatd/labwc input devices

# Установка зависимостей
apt-get update
BASE_PACKAGES=(
    python3-pip python3-venv python3-dev
    sqlite3 libsqlite3-dev
    mpv ffmpeg yt-dlp
    socat
    nginx git
    acl libdrm-dev
    xauth
)
WAYLAND_PACKAGES=()
if [ "$DSIGN_DISPLAY_BACKEND" = "wayland" ]; then
    WAYLAND_PACKAGES=(labwc imv seatd wayland-protocols grim foot wtype wlrctl swayidle mesa-vulkan-drivers vulkan-tools)
fi
apt-get install -y "${BASE_PACKAGES[@]}" "${WAYLAND_PACKAGES[@]}"

# Создание структуры директорий
mkdir -p "$PROJECT_DIR" "$VENV_DIR" "$CONFIG_DIR" "$LOG_DIR" "$UPLOAD_DIR" "$DB_DIR" "$X11_USER_DIR"
chown -R "$DSIGN_USER:$DSIGN_USER" "/home/dsign" "$DB_DIR" "$LOG_DIR" "$X11_USER_DIR"
chmod -R 775 "$UPLOAD_DIR" "$DB_DIR"

# Настройка X11 авторизации
sudo -u "$DSIGN_USER" xauth generate :0 . trusted
chown "$DSIGN_USER:$DSIGN_USER" "$X11_USER_DIR/Xauthority"
ln -sf "$X11_USER_DIR/Xauthority" "/home/dsign/.Xauthority"

# Настройка ACL
setfacl -R -m u:"$WWW_USER":rwX "$PROJECT_DIR" "$UPLOAD_DIR" "$DB_DIR"
setfacl -Rd -m u:"$WWW_USER":rwX "$PROJECT_DIR" "$UPLOAD_DIR" "$DB_DIR"

# Клонирование репозитория
sudo -u "$DSIGN_USER" git clone https://github.com/kirpodolak/dsign.git "$PROJECT_DIR"

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
    MPV_SOCKET = '/tmp/mpv-socket'
EOL

# Инициализация базы данных
cat > "$PROJECT_DIR/init_db.py" <<EOL
from app import create_app
from extensions import db
import models

app = create_app('$CONFIG_DIR/config.py')

with app.app_context():
    db.create_all()
    
    # Создаем администратора по умолчанию
    admin = models.User.query.filter_by(username='admin').first()
    if not admin:
        admin = models.User(username='admin', is_admin=True)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Создан администратор: admin/admin123")
EOL

# Выполнение инициализации БД
sudo -u "$DSIGN_USER" "$VENV_DIR/bin/python" "$PROJECT_DIR/init_db.py"
rm "$PROJECT_DIR/init_db.py"

# Systemd units, helpers, sudoers — via deploy manifest (D0)
# MPV minimal + labwc + config dirs (files applied via manifest below)
mkdir -p "$DB_DIR/mpv-minimal/profiles" "$DB_DIR/labwc" "$DB_DIR/config"
chown -R "$DSIGN_USER:video" "$DB_DIR/mpv-minimal"
chmod 775 "$DB_DIR/mpv-minimal"
chown -R "$DSIGN_USER:$DSIGN_USER" "$DB_DIR/labwc"
chown "$DSIGN_USER:$DSIGN_USER" "$DB_DIR/config"

# Deploy manifest: install tooling, then apply repo → system paths (D0)
for _tool in dsign-deploy-manifest.py dsign-verify-install dsign-apply-install; do
    install -m 0755 "$PROJECT_DIR/usr/local/bin/$_tool" "/usr/local/bin/$_tool"
    sed -i 's/\r$//' "/usr/local/bin/$_tool"
    chown root:root "/usr/local/bin/$_tool"
done
export DSIGN_PROJECT_ROOT="$PROJECT_DIR"
export DSIGN_VENV="$VENV_DIR"
export DSIGN_DB_DIR="$DB_DIR"
export DSIGN_DISPLAY_BACKEND
if ! dsign-apply-install -q; then
    echo "WARN: dsign-apply-install finished with warnings — run: dsign-verify-install" >&2
fi

# API monitoring token (/etc/dsign/api.env) — create once, never overwrite on reinstall
if command -v dsign-api-token >/dev/null 2>&1; then
    dsign-api-token install -q
else
    install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-api-token" /usr/local/bin/dsign-api-token
    sed -i 's/\r$//' /usr/local/bin/dsign-api-token
    dsign-api-token install -q
fi

# Настройка прав на DRI устройства
cat > /etc/udev/rules.d/99-dsign.rules <<EOL
KERNEL=="card[0-9]*", GROUP="video", MODE="0660"
KERNEL=="renderD*", GROUP="video", MODE="0660"
EOL

udevadm control --reload-rules
udevadm trigger

systemctl daemon-reload
systemctl enable screenshot.service screenshot.timer 2>/dev/null || true
systemctl start screenshot.timer 2>/dev/null || true
if [ "$DSIGN_DISPLAY_BACKEND" = "wayland" ]; then
    systemctl enable seatd.service 2>/dev/null || true
    # Headless signage: logind creates /run/user/UID at boot (compositor also pre-creates it as root).
    loginctl enable-linger dsign 2>/dev/null || true
    systemctl enable dsign-compositor.service dsign-logo.service dsign-mpv-wayland.service
    systemctl disable dsign-mpv.service 2>/dev/null || true
    systemctl stop dsign-mpv.service 2>/dev/null || true
    if ! systemctl mask dsign-mpv.service 2>/dev/null; then
        ln -sf /dev/null /etc/systemd/system/dsign-mpv.service
    fi
    systemctl daemon-reload
    systemctl enable digital-signage.service dsign-network-assistant.service
    systemctl disable dsign-show-startup-ip.service || true
    systemctl start seatd.service 2>/dev/null || true
    systemctl start dsign-compositor.service dsign-logo.service dsign-mpv-wayland.service
    systemctl start dsign-network-assistant.service digital-signage.service
else
    rm -f /etc/systemd/system/digital-signage.service.d/wayland.conf 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable digital-signage.service dsign-mpv.service dsign-network-assistant.service
    systemctl disable dsign-show-startup-ip.service || true
    systemctl start dsign-network-assistant.service dsign-mpv.service digital-signage.service
fi

# Настройка Nginx
cat > /etc/nginx/sites-available/dsign <<EOL
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
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

ln -s /etc/nginx/sites-available/dsign /etc/nginx/sites-enabled/
systemctl restart nginx

echo "Установка завершена!"
echo "----------------------------------------"
echo "База данных: $DB_FILE"
echo "Администратор: admin/admin123"
echo "Сервисы:"
echo "  Веб-интерфейс: systemctl status digital-signage.service"
echo "  API token:     sudo dsign-api-token show   # GET /api/health Bearer auth"
if [ "$DSIGN_DISPLAY_BACKEND" = "wayland" ]; then
    echo "  Compositor:    systemctl status dsign-compositor.service"
    echo "  Logo (imv):    systemctl status dsign-logo.service"
    echo "  MPV (Wayland): systemctl status dsign-mpv-wayland.service"
    echo "  Screenshot:    systemctl status screenshot.timer  (grim + MPV IPC fallback)"
else
    echo "  MPV плеер:     systemctl status dsign-mpv.service"
    echo "  Wayland pilot: DSIGN_DISPLAY_BACKEND=wayland $0"
fi
echo "----------------------------------------"
