#!/bin/bash

# Проверка на root
if [ "$(id -u)" -ne 0 ]; then
  echo "Этот скрипт должен запускаться с правами root" >&2
  exit 1
fi

# Конфигурация
DSIGN_USER="dsign"
WWW_USER="www-data"
DSIGN_DIR="/opt/dsign"
VENV_DIR="$DSIGN_DIR/venv"
CONFIG_DIR="/etc/dsign"
LOG_DIR="/var/log/dsign"
UPLOAD_DIR="/var/www/dsign/uploads"
DB_NAME="dsign"
DB_USER="dsign"
DB_PASS=$(openssl rand -hex 12)

# Установка зависимостей системы
echo "Установка системных зависимостей..."
apt-get update
apt-get install -y \
    python3-pip python3-venv python3-dev \
    libpq-dev postgresql postgresql-contrib \
    mpv ffmpeg \
    nginx git

# Создание пользователей и групп
if ! id "$DSIGN_USER" &>/dev/null; then
    useradd -r -s /bin/false "$DSIGN_USER"
fi
usermod -a -G "$DSIGN_USER" "$WWW_USER"
usermod -a -G "$WWW_USER" "$DSIGN_USER"

# Создание директорий
echo "Создание структуры директорий..."
mkdir -p "$DSIGN_DIR" "$CONFIG_DIR" "$LOG_DIR" "$UPLOAD_DIR"
chown -R "$DSIGN_USER:$DSIGN_USER" "$DSIGN_DIR" "$LOG_DIR"
chown -R "$WWW_USER:$WWW_USER" "$UPLOAD_DIR"
chmod -R 775 "$UPLOAD_DIR"

# Клонирование репозитория
echo "Клонирование репозитория..."
git clone https://github.com/kirpodolak/dsign.git "$DSIGN_DIR"
chown -R "$DSIGN_USER:$DSIGN_USER" "$DSIGN_DIR"

# Настройка виртуального окружения
echo "Создание виртуального окружения..."
sudo -u "$DSIGN_USER" python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip wheel
pip install -r "$DSIGN_DIR/requirements.txt"
deactivate

# Настройка базы данных
echo "Настройка PostgreSQL..."
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# Создание конфигурационного файла
echo "Создание конфигурации..."
cat > "$CONFIG_DIR/config.py" <<EOL
import os

class Config:
    SECRET_KEY = '$(openssl rand -hex 16)'
    SQLALCHEMY_DATABASE_URI = 'postgresql://$DB_USER:$DB_PASS@localhost/$DB_NAME'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = '$UPLOAD_DIR'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    LOG_DIR = '$LOG_DIR'
    MPV_SOCKET = '/tmp/mpv-socket'
EOL

chown "$DSIGN_USER:$DSIGN_USER" "$CONFIG_DIR/config.py"
chmod 640 "$CONFIG_DIR/config.py"

# Инициализация базы данных
echo "Инициализация базы данных..."
export DSIGN_CONFIG="$CONFIG_DIR/config.py"
sudo -u "$DSIGN_USER" -E "$VENV_DIR/bin/python" "$DSIGN_DIR/manage.py db upgrade"

# Настройка systemd службы
echo "Настройка службы systemd..."
cat > /etc/systemd/system/dsign.service <<EOL
[Unit]
Description=DSign Digital Signage Service
After=network.target postgresql.service

[Service]
User=$DSIGN_USER
Group=$DSIGN_USER
WorkingDirectory=$DSIGN_DIR
Environment="DSIGN_CONFIG=$CONFIG_DIR/config.py"
ExecStart=$VENV_DIR/bin/gunicorn -w 4 -b 127.0.0.1:8000 "app:create_app()"
Restart=always

[Install]
WantedBy=multi-user.target
EOL

systemctl daemon-reload
systemctl enable dsign.service
systemctl start dsign.service

# Настройка Nginx
echo "Настройка Nginx..."
cat > /etc/nginx/sites-available/dsign <<EOL
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location /uploads/ {
        alias $UPLOAD_DIR/;
        expires 30d;
    }
}
EOL

ln -s /etc/nginx/sites-available/dsign /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default
systemctl restart nginx

# Настройка cron для очистки
echo "Настройка регулярных задач..."
cat > /etc/cron.d/dsign <<EOL
0 3 * * * $DSIGN_USER $VENV_DIR/bin/python $DSIGN_DIR/manage.py cleanup
EOL

echo "Установка завершена!"
echo "Данные для доступа к БД:"
echo "База данных: $DB_NAME"
echo "Пользователь: $DB_USER"
echo "Пароль: $DB_PASS"
echo "Приложение доступно по адресу: http://сервер"
