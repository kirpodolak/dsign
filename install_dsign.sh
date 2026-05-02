#!/bin/bash

# Проверка на root
if [ "$(id -u)" -ne 0 ]; then
  echo "Этот скрипт должен запускаться с правами root" >&2
  exit 1
fi

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
    socat \
    nginx git \
    acl libdrm-dev \
    xauth  # Для X11 авторизации

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

# Создание systemd сервисов с точными настройками

# Digital Signage Service — canonical unit from repo (no Requires=/dev/dri card0 — breaks x86/no-Pi installs).
INSTALL_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC=""
for cand in "$PROJECT_DIR/etc/systemd/system/digital-signage.service" "$INSTALL_SRC/etc/systemd/system/digital-signage.service"; do
    [[ -f "$cand" ]] && UNIT_SRC="$cand" && break
done
if [[ -n "$UNIT_SRC" ]]; then
    install -m 0644 "$UNIT_SRC" /etc/systemd/system/digital-signage.service
else
    echo "WARN: digital-signage.service template not found — skip install" >&2
fi
# Repo clone lives in PROJECT_DIR=/home/dsign/dsign → package is PROJECT_DIR/dsign/; PYTHONPATH root is PROJECT_DIR.
sed -i \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$PROJECT_DIR|" \
    -e "s|^Environment=DSIGN_PROJECT_ROOT=.*|Environment=DSIGN_PROJECT_ROOT=$PROJECT_DIR|" \
    -e "s|^ExecStart=.*|ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/run.py|" \
    /etc/systemd/system/digital-signage.service 2>/dev/null || true

# MPV minimal config under /var/lib/dsign (owned by dsign — editable without root)
mkdir -p "$DB_DIR/mpv-minimal"
if [ ! -f "$DB_DIR/mpv-minimal/mpv.conf" ]; then
    install -m 0644 "$PROJECT_DIR/etc/dsign/mpv-minimal/mpv.conf" "$DB_DIR/mpv-minimal/mpv.conf"
fi
chown -R "$DSIGN_USER:video" "$DB_DIR/mpv-minimal"
chmod 775 "$DB_DIR/mpv-minimal"
chmod 664 "$DB_DIR/mpv-minimal/mpv.conf" 2>/dev/null || true

# MPV Player Service
cat > /etc/systemd/system/dsign-mpv.service <<EOL
[Unit]
Description=Digital Signage MPV Player
After=network.target getty@tty1.service
Conflicts=getty@tty1.service

[Service]
User=$DSIGN_USER
Group=video
Type=simple
SupplementaryGroups=tty
PermissionsStartOnly=true
Environment="TERM=linux"
WorkingDirectory=/home/dsign
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
UMask=0002
ExecStartPre=/bin/chvt 1
ExecStartPre=/bin/mkdir -p /var/lib/dsign/mpv /var/lib/dsign/mpv-minimal
ExecStartPre=/bin/chown -R dsign:video /var/lib/dsign/mpv /var/lib/dsign/mpv-minimal
ExecStartPre=/bin/chmod 775 /var/lib/dsign/mpv /var/lib/dsign/mpv-minimal
ExecStartPre=/bin/rm -f /var/lib/dsign/mpv/socket
ExecStart=/usr/bin/mpv --idle=yes --no-terminal --config-dir=/var/lib/dsign/mpv-minimal --no-osc --no-input-default-bindings --input-ipc-server=/var/lib/dsign/mpv/socket --vo=drm --drm-connector=HDMI-A-1 --drm-mode=1920x1080@60 --drm-draw-plane=primary --drm-drmprime-video-plane=primary --fullscreen --demuxer-lavf-o=safe=0 --hwdec=v4l2m2m-copy --vd-lavc-dr=no --interpolation=no --deband=no --scale=bilinear --dscale=bilinear --cscale=bilinear --video-sync=display-vdrop --ao=alsa --audio-device=alsa/plughw:CARD=vc4hdmi,DEV=0 --log-file=/var/log/dsign/mpv.log
ExecStartPost=-/usr/local/bin/dsign-show-startup-ip
Restart=always
RestartSec=5s
StartLimitInterval=60s
StartLimitBurst=3

[Install]
WantedBy=multi-user.target
EOL

# Network assistant helper (OSD on content screen via MPV IPC)
install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-network-assistant" /usr/local/bin/dsign-network-assistant
sed -i 's/\r$//' /usr/local/bin/dsign-network-assistant
install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-show-startup-ip" /usr/local/bin/dsign-show-startup-ip
sed -i 's/\r$//' /usr/local/bin/dsign-show-startup-ip
chown root:root /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip

cat > /etc/systemd/system/dsign-network-assistant.service <<EOL
[Unit]
Description=Digital Signage Network Assistant (OSD)
After=network.target
Before=digital-signage.service dsign-mpv.service
Wants=network.target

[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/local/bin/dsign-network-assistant
Environment=DSIGN_NETWORK_PROMPT_TIMEOUT_SEC=120

[Install]
WantedBy=multi-user.target
EOL

cat > /etc/systemd/system/dsign-show-startup-ip.service <<EOL
[Unit]
Description=Digital Signage Startup IP OSD helper
After=dsign-mpv.service dsign-network-assistant.service
Wants=dsign-network-assistant.service

[Service]
Type=oneshot
User=$DSIGN_USER
Group=$DSIGN_USER
ExecStart=/usr/local/bin/dsign-show-startup-ip

[Install]
WantedBy=multi-user.target
EOL

# Настройка прав на DRI устройства
cat > /etc/udev/rules.d/99-dsign.rules <<EOL
KERNEL=="card0", GROUP="video", MODE="0660"
KERNEL=="renderD128", GROUP="video", MODE="0660"
EOL

udevadm control --reload-rules
udevadm trigger

systemctl daemon-reload
systemctl enable digital-signage.service dsign-mpv.service dsign-network-assistant.service
systemctl disable dsign-show-startup-ip.service || true
systemctl start dsign-network-assistant.service dsign-mpv.service digital-signage.service

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
echo "  MPV плеер:     systemctl status dsign-mpv.service"
echo "----------------------------------------"
