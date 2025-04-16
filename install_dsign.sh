#!/bin/bash

# Проверка на root
if [ "$(id -u)" -ne 0 ]; then
  echo "Этот скрипт должен запускаться с правами root" >&2
  exit 1
fi

# Конфигурация
DSIGN_USER="dsign"
WWW_USER="www-data"
PROJECT_DIR="/home/dsign/dsign"  # Оригинальный путь проекта
VENV_DIR="/home/dsign/venv"
CONFIG_DIR="/home/dsign/config"
LOG_DIR="/var/log/dsign"
UPLOAD_DIR="/var/lib/dsign/media"
DB_DIR="/var/lib/dsign"
SETTINGS_FILE="/var/lib/dsign/settings.json"

# Создание пользователя и групп
if ! id "$DSIGN_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$DSIGN_USER"
fi
usermod -a -G "$DSIGN_USER" "$WWW_USER"
usermod -a -G "$WWW_USER" "$DSIGN_USER"

# Установка зависимостей
apt-get update
apt-get install -y \
    python3-pip python3-venv python3-dev \
    libpq-dev postgresql postgresql-contrib \
    mpv ffmpeg \
    nginx git \
    acl  # Для расширенного управления правами

# Создание структуры директорий
mkdir -p "$PROJECT_DIR" "$VENV_DIR" "$CONFIG_DIR" "$LOG_DIR" "$UPLOAD_DIR" "$DB_DIR"
chown -R "$DSIGN_USER:$DSIGN_USER" "/home/dsign" "$DB_DIR" "$LOG_DIR"
chmod -R 775 "$UPLOAD_DIR"

# Настройка ACL для совместного доступа
setfacl -R -m u:"$WWW_USER":rwX "$PROJECT_DIR" "$UPLOAD_DIR" "$DB_DIR"
setfacl -Rd -m u:"$WWW_USER":rwX "$PROJECT_DIR" "$UPLOAD_DIR" "$DB_DIR"

# Клонирование репозитория
sudo -u "$DSIGN_USER" git clone https://github.com/kirpodolak/dsign.git "$PROJECT_DIR"

# Создание необходимых файлов
touch "$SETTINGS_FILE" "$LOG_DIR/dsign.log" "$LOG_DIR/app.log"
chown "$DSIGN_USER:$DSIGN_USER" "$SETTINGS_FILE" "$LOG_DIR/dsign.log" "$LOG_DIR/app.log"

# Настройка виртуального окружения
sudo -u "$DSIGN_USER" python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip wheel
pip install -r "$PROJECT_DIR/requirements.txt"
deactivate

# Конфигурационные файлы
cat > "$CONFIG_DIR/config.py" <<EOL
import os

class Config:
    SECRET_KEY = '$(openssl rand -hex 32)'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///$DB_DIR/database.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = '$UPLOAD_DIR'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    LOG_DIR = '$LOG_DIR'
    MPV_SOCKET = '/tmp/mpv-socket'
EOL

# Systemd сервисы
cat > /etc/systemd/system/digital-signage.service <<EOL
[Unit]
Description=Digital Signage Web Service
After=network.target

[Service]
User=$DSIGN_USER
Group=$DSIGN_USER
WorkingDirectory=$PROJECT_DIR
Environment="FLASK_APP=$PROJECT_DIR/server.py"
Environment="FLASK_ENV=production"
ExecStart=$VENV_DIR/bin/gunicorn -w 4 -b 127.0.0.1:8000 "server:app"
Restart=always

[Install]
WantedBy=multi-user.target
EOL

cat > /etc/systemd/system/dsign-mpv.service <<EOL
[Unit]
Description=Digital Signage MPV Service
After=network.target

[Service]
User=$DSIGN_USER
Group=$DSIGN_USER
WorkingDirectory=$PROJECT_DIR
Environment="PYTHONPATH=$PROJECT_DIR"
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/services/playback_service.py
Restart=always

[Install]
WantedBy=multi-user.target
EOL

systemctl daemon-reload
systemctl enable digital-signage.service dsign-mpv.service
systemctl start digital-signage.service dsign-mpv.service

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

ln -s /etc/nginx/sites-available/dsign /etc/nginx/sites-enabled/
systemctl restart nginx

# Инициализация базы данных
sudo -u "$DSIGN_USER" "$VENV_DIR/bin/python" "$PROJECT_DIR/manage.py db upgrade"

echo "Установка завершена!"
echo "Проект развернут в: $PROJECT_DIR"
echo "Сервисы:"
echo "  Веб-интерфейс: systemctl status digital-signage.service"
echo "  MPV сервис:    systemctl status dsign-mpv.service"
