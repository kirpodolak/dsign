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
    WAYLAND_PACKAGES=(labwc imv seatd wayland-protocols grim foot wtype wlrctl)
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
# Align ExecStartPre MPV socket wait path with PlaybackConstants.SOCKET_PATH (default /var/lib/dsign/mpv/socket).
sed -i \
    -e "s|/var/lib/dsign/mpv/socket|$DB_DIR/mpv/socket|g" \
    /etc/systemd/system/digital-signage.service 2>/dev/null || true

# MPV minimal config under /var/lib/dsign (owned by dsign — editable without root)
mkdir -p "$DB_DIR/mpv-minimal/profiles"
if [ ! -f "$DB_DIR/mpv-minimal/mpv.conf" ]; then
    install -m 0644 "$PROJECT_DIR/etc/dsign/mpv-minimal/mpv.conf" "$DB_DIR/mpv-minimal/mpv.conf"
fi
for _prof in intel-iris-xe-high-quality.conf intel-iris-xe-balanced.conf intel-iris-xe-balanced-vulkan.conf; do
    if [ -f "$PROJECT_DIR/etc/dsign/mpv-minimal/profiles/$_prof" ]; then
        install -m 0644 "$PROJECT_DIR/etc/dsign/mpv-minimal/profiles/$_prof" \
            "$DB_DIR/mpv-minimal/profiles/$_prof"
    fi
done
chown -R "$DSIGN_USER:video" "$DB_DIR/mpv-minimal"
chmod 775 "$DB_DIR/mpv-minimal"
chmod 664 "$DB_DIR/mpv-minimal/mpv.conf" 2>/dev/null || true

install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-mpv-launch" /usr/local/bin/dsign-mpv-launch
sed -i 's/\r$//' /usr/local/bin/dsign-mpv-launch
chown root:root /usr/local/bin/dsign-mpv-launch

# Wayland/labwc stack scripts (optional; used when DSIGN_DISPLAY_BACKEND=wayland)
for _wl_script in dsign-mpv-launch-wayland dsign-labwc-autostart dsign-logo-launch dsign-mpv-recover; do
    if [ -f "$PROJECT_DIR/usr/local/bin/$_wl_script" ]; then
        install -m 0755 "$PROJECT_DIR/usr/local/bin/$_wl_script" "/usr/local/bin/$_wl_script"
        sed -i 's/\r$//' "/usr/local/bin/$_wl_script"
        chown root:root "/usr/local/bin/$_wl_script"
    fi
done

# Wayland systemd units + labwc config (installed always; enable only in wayland mode)
for _wl_unit in dsign-compositor.service dsign-logo.service dsign-mpv-wayland.service; do
    if [ -f "$PROJECT_DIR/etc/systemd/system/$_wl_unit" ]; then
        install -m 0644 "$PROJECT_DIR/etc/systemd/system/$_wl_unit" "/etc/systemd/system/$_wl_unit"
    fi
done
mkdir -p "$DB_DIR/labwc"
if [ -f "$PROJECT_DIR/etc/dsign/labwc/rc.xml" ]; then
    install -m 0644 "$PROJECT_DIR/etc/dsign/labwc/rc.xml" "$DB_DIR/labwc/rc.xml"
fi
if [ -f "$PROJECT_DIR/etc/dsign/labwc/autostart" ]; then
    install -m 0755 "$PROJECT_DIR/etc/dsign/labwc/autostart" "$DB_DIR/labwc/autostart"
    sed -i 's/\r$//' "$DB_DIR/labwc/autostart"
fi
chown -R "$DSIGN_USER:$DSIGN_USER" "$DB_DIR/labwc"
mkdir -p "$DB_DIR/config"
if [ -f "$PROJECT_DIR/etc/dsign/wayland.env.example" ] && [ ! -f "$DB_DIR/config/wayland.env" ]; then
    _dri_card=""
    for _c in /dev/dri/card[0-9]*; do
        [ -e "$_c" ] || continue
        _base="$(basename "$_c")"
        for _st in /sys/class/drm/"${_base}"-*/status; do
            [ -r "$_st" ] || continue
            if [ "$(cat "$_st" 2>/dev/null)" = "connected" ]; then
                _dri_card="$_c"
                break 2
            fi
        done
    done
    if [ -z "$_dri_card" ]; then
        for _c in /dev/dri/card[0-9]*; do
            [ -e "$_c" ] && _dri_card="$_c" && break
        done
    fi
    _dri_card="${_dri_card:-/dev/dri/card0}"
    install -m 0644 "$PROJECT_DIR/etc/dsign/wayland.env.example" "$DB_DIR/config/wayland.env"
    sed -i "s|^WLR_DRM_DEVICES=.*|WLR_DRM_DEVICES=${_dri_card}|" "$DB_DIR/config/wayland.env"
    chown "$DSIGN_USER:$DSIGN_USER" "$DB_DIR/config/wayland.env"
fi
if [ -f "$DB_DIR/config/wayland.env" ] && grep -q '^WLR_RENDERER=gles2' "$DB_DIR/config/wayland.env" 2>/dev/null; then
    sed -i 's/^WLR_RENDERER=.*/WLR_RENDERER=pixman/' "$DB_DIR/config/wayland.env"
fi
if [ "$DSIGN_DISPLAY_BACKEND" = "wayland" ]; then
    install -d /etc/systemd/system/digital-signage.service.d
    if [ -f "$PROJECT_DIR/etc/systemd/system/digital-signage.service.d/wayland.conf" ]; then
        install -m 0644 "$PROJECT_DIR/etc/systemd/system/digital-signage.service.d/wayland.conf" \
            /etc/systemd/system/digital-signage.service.d/wayland.conf
    fi
    install -d /etc/systemd/system/screenshot.service.d
    if [ -f "$PROJECT_DIR/etc/systemd/system/screenshot.service.d/wayland.conf" ]; then
        install -m 0644 "$PROJECT_DIR/etc/systemd/system/screenshot.service.d/wayland.conf" \
            /etc/systemd/system/screenshot.service.d/wayland.conf
    fi
    install -d /etc/systemd/system/dsign-show-startup-ip.service.d
    if [ -f "$PROJECT_DIR/etc/systemd/system/dsign-show-startup-ip.service.d/wayland.conf" ]; then
        install -m 0644 "$PROJECT_DIR/etc/systemd/system/dsign-show-startup-ip.service.d/wayland.conf" \
            /etc/systemd/system/dsign-show-startup-ip.service.d/wayland.conf
    fi
fi
if [ -f "$PROJECT_DIR/usr/local/bin/dsign-capture" ]; then
    install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-capture" /usr/local/bin/dsign-capture
    sed -i 's/\r$//' /usr/local/bin/dsign-capture
fi
if [ -f "$PROJECT_DIR/etc/systemd/system/screenshot.service" ]; then
    install -m 0644 "$PROJECT_DIR/etc/systemd/system/screenshot.service" /etc/systemd/system/screenshot.service
fi
if [ -f "$PROJECT_DIR/etc/systemd/system/screenshot.timer" ]; then
    install -m 0644 "$PROJECT_DIR/etc/systemd/system/screenshot.timer" /etc/systemd/system/screenshot.timer
fi
if [ -f "$PROJECT_DIR/etc/sudoers.d/dsign-screenshot" ]; then
    install -m 0440 "$PROJECT_DIR/etc/sudoers.d/dsign-screenshot" /etc/sudoers.d/dsign-screenshot
    visudo -cf /etc/sudoers.d/dsign-screenshot 2>/dev/null || true
fi
if [ -f "$PROJECT_DIR/etc/sudoers.d/dsign-systemctl" ]; then
    install -m 0440 "$PROJECT_DIR/etc/sudoers.d/dsign-systemctl" /etc/sudoers.d/dsign-systemctl
    visudo -cf /etc/sudoers.d/dsign-systemctl 2>/dev/null || true
fi
if [ -f "$PROJECT_DIR/etc/sudoers.d/dsign-mpv-restart" ]; then
    install -m 0440 "$PROJECT_DIR/etc/sudoers.d/dsign-mpv-restart" /etc/sudoers.d/dsign-mpv-restart
    visudo -cf /etc/sudoers.d/dsign-mpv-restart 2>/dev/null || true
fi

# MPV Player Service (DRM / vo=drm — default stack)
cat > /etc/systemd/system/dsign-mpv.service <<EOL
[Unit]
Description=Digital Signage MPV Player
After=network.target getty@tty1.service
Conflicts=getty@tty1.service

[Service]
User=$DSIGN_USER
Group=video
Type=simple
SupplementaryGroups=tty audio
PermissionsStartOnly=true
Environment="TERM=linux"
Environment="DSIGN_MPV_PROFILE=signboard"
Environment="DSIGN_MPV_SIGNBOARD_VARIANT=pi"
WorkingDirectory=/home/dsign
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
UMask=0002
ExecStartPre=/bin/chvt 1
ExecStartPre=/bin/mkdir -p /var/lib/dsign/mpv /var/lib/dsign/mpv/archive /var/lib/dsign/mpv-minimal
ExecStartPre=/bin/chown -R dsign:video /var/lib/dsign/mpv /var/lib/dsign/mpv-minimal
ExecStartPre=/bin/chmod 775 /var/lib/dsign/mpv /var/lib/dsign/mpv-minimal
ExecStartPre=/bin/rm -f /var/lib/dsign/mpv/socket
ExecStart=/usr/local/bin/dsign-mpv-launch
# Startup IP OSD helper should run on every MPV (re)start.
# Use systemd oneshot unit so it doesn't get stuck active(exited).
ExecStartPost=-/bin/systemctl --no-block restart dsign-show-startup-ip.service
ExecStopPost=-/bin/bash -c "tr -d '\\r' < /usr/local/bin/dsign-mpv-archive-log | bash"
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
install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-wifi-on-display" /usr/local/bin/dsign-wifi-on-display
install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-nmtui-tty" /usr/local/bin/dsign-nmtui-tty
if [ -f "$PROJECT_DIR/usr/local/bin/dsign-nmtui-wayland" ]; then
    install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-nmtui-wayland" /usr/local/bin/dsign-nmtui-wayland
    sed -i 's/\r$//' /usr/local/bin/dsign-nmtui-wayland
fi
sed -i 's/\r$//' /usr/local/bin/dsign-show-startup-ip
sed -i 's/\r$//' /usr/local/bin/dsign-wifi-on-display
sed -i 's/\r$//' /usr/local/bin/dsign-nmtui-tty
for _diag in dsign-diagnose-wifi-on-display dsign-diagnose-playback dsign-diagnose-compositor; do
    if [ -f "$PROJECT_DIR/usr/local/bin/$_diag" ]; then
        install -m 0755 "$PROJECT_DIR/usr/local/bin/$_diag" "/usr/local/bin/$_diag"
        sed -i 's/\r$//' "/usr/local/bin/$_diag"
    fi
done
install -m 0644 "$PROJECT_DIR/etc/tmpfiles.d/dsign.conf" /etc/tmpfiles.d/dsign.conf
systemd-tmpfiles --create /etc/tmpfiles.d/dsign.conf 2>/dev/null || true
install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-mpv-archive-log" /usr/local/bin/dsign-mpv-archive-log
sed -i 's/\r$//' /usr/local/bin/dsign-mpv-archive-log
chown root:root /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip /usr/local/bin/dsign-wifi-on-display /usr/local/bin/dsign-nmtui-tty /usr/local/bin/dsign-nmtui-wayland /usr/local/bin/dsign-mpv-launch /usr/local/bin/dsign-mpv-archive-log 2>/dev/null || \
chown root:root /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip /usr/local/bin/dsign-wifi-on-display /usr/local/bin/dsign-nmtui-tty /usr/local/bin/dsign-mpv-launch /usr/local/bin/dsign-mpv-archive-log
# Manual deploys from Windows editors often leave CRLF — breaks bash (set: invalid option).
for _dsign_bin in /usr/local/bin/dsign-*; do
    [ -f "$_dsign_bin" ] || continue
    sed -i 's/\r$//' "$_dsign_bin"
    chmod 755 "$_dsign_bin" 2>/dev/null || true
done

mkdir -p /var/lib/dsign/config
chown "$DSIGN_USER:$DSIGN_USER" /var/lib/dsign/config

cat > /etc/systemd/system/dsign-network-assistant.service <<EOL
[Unit]
Description=Digital Signage Network Assistant (OSD)
After=network.target
Before=digital-signage.service dsign-mpv.service
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
Group=root
ExecStart=/usr/local/bin/dsign-network-assistant
EnvironmentFile=-/var/lib/dsign/config/network-assistant.env
Environment=DSIGN_NETWORK_PROMPT_TIMEOUT_SEC=120
Environment=DSIGN_NETWORK_STATUS_DISPLAY_SEC=10
Environment=DSIGN_STARTUP_IP_FILE=/tmp/dsign-startup-ip.txt
Environment=DSIGN_NETWORK_STATUS_FILE=/run/dsign/network-status.env

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
RemainAfterExit=no
User=$DSIGN_USER
Group=$DSIGN_USER
ExecStart=/usr/local/bin/dsign-show-startup-ip

[Install]
WantedBy=multi-user.target
EOL

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
    systemctl enable dsign-compositor.service dsign-logo.service dsign-mpv-wayland.service
    systemctl disable dsign-mpv.service 2>/dev/null || true
    systemctl stop dsign-mpv.service 2>/dev/null || true
    if ! systemctl mask dsign-mpv.service 2>/dev/null; then
        ln -sf /dev/null /etc/systemd/system/dsign-mpv.service
    fi
    if [ -f "$PROJECT_DIR/usr/local/bin/dsign-disable-drm-mpv" ]; then
        install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-disable-drm-mpv" /usr/local/bin/dsign-disable-drm-mpv
        sed -i 's/\r$//' /usr/local/bin/dsign-disable-drm-mpv
    fi
    if [ -f "$PROJECT_DIR/usr/local/bin/dsign-fix-deploy-scripts" ]; then
        install -m 0755 "$PROJECT_DIR/usr/local/bin/dsign-fix-deploy-scripts" /usr/local/bin/dsign-fix-deploy-scripts
        sed -i 's/\r$//' /usr/local/bin/dsign-fix-deploy-scripts
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
