Перезапуск службы
sudo systemctl daemon-reload
sudo systemctl restart digital-signage.service
#sudo systemctl restart dsign-mpv.service
sudo systemctl restart screenshot.service
sudo systemctl restart screenshot.timer
sudo systemctl restart dsign-network-assistant.service
sudo systemctl restart dsign-show-startup-ip.service
sudo systemctl restart dsign-compositor.service
sudo systemctl restart dsign-mpv-wayland.service


Статус службы
sudo systemctl status digital-signage.service
sudo systemctl status dsign-mpv.service
sudo systemctl status screenshot.service
sudo systemctl status screenshot.timer
sudo systemctl status dsign-network-assistant.service
sudo systemctl status dsign-show-startup-ip.service
sudo systemctl status dsign-compositor.service
sudo systemctl status dsign-mpv-wayland.service

#Логи в реальном времени
sudo journalctl -u digital-signage.service -f 
sudo journalctl -u dsign-mpv.service -f 
sudo journalctl -u screenshot.service -f 
sudo journalctl -u screenshot.timer -f 
sudo journalctl -u dsign-compositor.service -f
sudo journalctl -u dsign-mpv-wayland.service -f
sudo journalctl -u dsign-compositor.service -f
sudo journalctl -u dsign-mpv-wayland.service -f

Фикс ручного копирования скриптов
sudo bash /usr/local/bin/dsign-fix-deploy-scripts
sudo bash /usr/local/bin/dsign-fix-sudoers

Диагностика
sudo bash /usr/local/bin/dsign-diagnose-playback
sudo bash /usr/local/bin/dsign-diagnose-wifi-on-display
sudo bash /usr/local/bin/dsign-diagnose-compositor
sudo bash /usr/local/bin/dsign-diagnose-audio

Токен для внешнего доступа
sudo dsign-api-token install #Создать /etc/dsign/api.env, если файла ещё нет
sudo dsign-api-token rotate #Новый токен (старый перестаёт работать)
sudo dsign-api-token rotate -y #То же без подтверждения
sudo dsign-api-token show #Показать текущий токен
sudo dsign-api-token status #Статус файла и сервиса (без вывода токена)

Юнит тесты
cd /home/dsign && source venv/bin/activate && python3 -m pytest tests/ -v

Ручной запуск сервера
python3 /home/dsign/server.py

cd /home/dsign
sudo pip3 uninstall dsign -y  # Удалите если уже установлен
sudo pip3 install -e .

Логи службы
tail -f /var/log/dsign/app.log
sudo journalctl -u digital-signage.service
sudo journalctl --vacuum-time=1s -u digital-signage.service #Очистка журнала логов
sudo journalctl --rotate #Это принудительно создает новый журнал, делая текущий журнал "старым".
sudo journalctl --vacuum-time=1s #Эта команда удаляет все журналы старше 1 секунды. Вы можете настроить это время на более длинное, если хотите удалить только старые логи.
journalctl -u sign-mpv.service --no-pager --lines=50 #последние 50 строк логов
journalctl -u digital-signage.service --no-pager --lines=50 #последние 50 строк логов

sudo journalctl -u digital-signage.service -f #Логи в реальном времени
sudo journalctl -u digital-signage.service --since "5 minutes ago" #Логи за последние 5 минут
sudo systemctl status digital-signage.service #Проверка состояния сервиса
sudo cat /var/log/dsign.log #Логи Flask-сервера

Логи MPV
sudo tail -n 50 /var/log/dsign/mpv.log

grep -r "action=" templates/ #Показывает какие есть запросы HTML
grep -r "fetch(" static/ #Показзывает существующие запросы API
python3 -v -c "from server import create_app" 2>&1 | grep import #текущие зависимости импортов
grep -r "@main_bp.route" /home/dsign/dsign/ #Проверка маршрутов
grep -r "@api_bp.route" /home/dsign/dsign/ #Проверка маршрутов

sqlite3 /var/lib/dsign/database.db
.tables
PRAGMA table_info(playlist_files);
SELECT * FROM playlist_files;

PRAGMA table_info(playlists);
.

Текущее состояние плейлиста
sqlite3 /var/lib/dsign/database.db "SELECT id, playlist_id, status, timestamp FROM playback_status;"

#Очистка кэша питона
find . -name "*.pyc" -delete
find . -name "__pycache__" -delete

ps aux | grep python #Проверка количества запущенный серверов
watch -n 1 "free -h && ps aux | grep python" #Проверка утечек памяти

Добавление пользователя
sudo sqlite3 /var/lib/dsign/database.db
INSERT INTO users (username, password) VALUES ('admin', 'pbkdf2:sha256:1000000$OZbhE7M1sutRW69m$8432527050e747b439f1dfc1dd73fac8549cd6386f0bbbc8ca5f2d5728b93852');
Пользователь admin с паролем admin123
Выходим из SQLite (Ctrl + D)

Генерация хэш пароля
sudo python3
from werkzeug.security import generate_password_hash
print(generate_password_hash("admin123", method="pbkdf2:sha256"))
pbkdf2:sha256:1000000$juU3nhYT6rMUDumx$1352c6766d0cb8bb007bc5606c8c2a13d84bc9a2508dde8fe96d998f23de829e

print(generate_password_hash("3786522", method="scrypt"))
scrypt:32768:8:1$AYukS6zms2CTauw3$c50bbc8c7d7b452710278774e2b00ff51826f2d7ba95deb19e0e72d7764414b27c386f5cce3731a765d18fb94faa583936e194ef500529b41855f4a0eb7008f8
INSERT INTO users (username, password, password_version) VALUES ('dsign', '1$AYukS6zms2CTauw3$c50bbc8c7d7b452710278774e2b00ff51826f2d7ba95deb19e0e72d7764414b27c386f5cce3731a765d18fb94faa583936e194ef500529b41855f4a0eb7008f8', 'scrypt');

REPLACE INTO users (username, password, password_version) VALUES ('dsign', '1$AYukS6zms2CTauw3$c50bbc8c7d7b452710278774e2b00ff51826f2d7ba95deb19e0e72d7764414b27c386f5cce3731a765d18fb94faa583936e194ef500529b41855f4a0eb7008f8', 'scrypt');

Запустите скрипт вручную, чтобы увидеть вывод ошибок в реальном времени:
/usr/bin/python3 /home/dsign/server.py
sudo usermod -aG video dsign #права на доступ к графическому оборудованию

Установка проекта
chmod +x setup.sh
./setup.sh

sudo timedatectl set-timezone Europe/Moscow #изменениче часового пояса на МСК

sudo nano /etc/systemd/system/digital-signage.service
sudo nano /etc/systemd/system/dsign-mpv.service
sudo nano /etc/systemd/system/screenshot.service
sudo nano /etc/systemd/system/screenshot.timer
sudo nano /usr/local/bin/dsign-capture

sudo nano /home/dsign/dsign/__init__.py
sudo nano /home/dsign/blueprints.py #Запросы
sudo nano /home/dsign/dsign/server.py
sudo nano /home/dsign/dsign/extensions.py #Дополнительные модули
sudo nano /home/dsign/dsign/forms.py #Формы ввода все
sudo nano /home/dsign/dsign/models.py #Матрицы для SQLAlchemy
sudo nano /home/dsign/dsign/routes.py #Обработка HTML запросов

Routes
sudo nano /home/dsign/dsign/routes/__init__.py
sudo nano /home/dsign/dsign/routes/auth_routes.py
sudo nano /home/dsign/dsign/routes/main_routes.py

Routes/api
sudo nano /home/dsign/dsign/routes/api/__init__.py
sudo nano /home/dsign/dsign/routes/api/api_routes.py
sudo nano /home/dsign/dsign/routes/api/logs.py

Services
sudo nano /home/dsign/dsign/services/__init__.py
sudo nano /home/dsign/dsign/services/auth.py
sudo nano /home/dsign/dsign/services/file_service.py
sudo nano /home/dsign/dsign/services/playback_service.py
  sudo nano /home/dsign/dsign/services/logo_management.py
  sudo nano /home/dsign/dsign/services/mpv_management.py
  sudo nano /home/dsign/dsign/services/playback_constants.py
  sudo nano /home/dsign/dsign/services/playback_utils.py
  sudo nano /home/dsign/dsign/services/playlist_management.py
  sudo nano /home/dsign/dsign/services/profile_management.py
sudo nano /home/dsign/dsign/services/logger.py
sudo nano /home/dsign/dsign/services/playlist_service.py
sudo nano /home/dsign/dsign/services/settings_service.py
sudo nano /home/dsign/dsign/services/sockets.py #Сокет запросы, вместо HTML
sudo nano /home/dsign/dsign/services/thumbnail_service.py
sudo nano /home/dsign/dsign/services/utils.py

Sockets
sudo nano /home/dsign/dsign/services/sockets/__init__.py
sudo nano /home/dsign/dsign/services/sockets/auth.py
sudo nano /home/dsign/dsign/services/sockets/connection.py
sudo nano /home/dsign/dsign/services/sockets/service.py
	sudo nano /home/dsign/dsign/services/sockets/handlers/__init__.py
	sudo nano /home/dsign/dsign/services/sockets/handlers/auth.py
	sudo nano /home/dsign/dsign/services/sockets/handlers/playback.py
	sudo nano /home/dsign/dsign/services/sockets/handlers/playlist.py
	sudo nano /home/dsign/dsign/services/sockets/handlers/system.py

config
sudo nano /home/dsign/dsign/config/__init__.py
sudo nano /home/dsign/dsign/config/mpv_settings_schema.py
sudo nano /home/dsign/dsign/config/config.py

JS
sudo nano /home/dsign/dsign/static/js/base.js
sudo nano /home/dsign/dsign/static/js/gallery.js
sudo nano /home/dsign/dsign/static/js/index.js
sudo nano /home/dsign/dsign/static/js/login.js
sudo nano /home/dsign/dsign/static/js/playlist.js
sudo nano /home/dsign/dsign/static/js/settings.js

sudo nano /home/dsign/dsign/static/js/utils/alerts.js
sudo nano /home/dsign/dsign/static/js/utils/api.js
sudo nano /home/dsign/dsign/static/js/utils/app-init.js
sudo nano /home/dsign/dsign/static/js/utils/auth.js
sudo nano /home/dsign/dsign/static/js/utils/helpers.js
sudo nano /home/dsign/dsign/static/js/utils/logging.js
sudo nano /home/dsign/dsign/static/js/utils/player-controls.js
sudo nano /home/dsign/dsign/static/js/utils/sockets.js

sudo nano /home/dsign/dsign/static/images/placeholder.jpg - заглушка если нет фото.

Дать доступы сюда:
sudo nano /var/lib/dsign/settings.json
sudo nano /var/lib/dsign/database.db
sudo nano /var/log/dsign.log
sudo nano /var/log/dsign/app.log
sudo nano /var/lib/dsign/media/

Фронт
sudo nano /home/dsign/dsign/templates/base.html
sudo nano /home/dsign/dsign/templates/index.html
sudo nano /home/dsign/dsign/templates/auth/login.html
sudo nano /home/dsign/dsign/templates/settings.html
sudo nano /home/dsign/dsign/templates/gallery.html
sudo nano /home/dsign/dsign/templates/playlist.html

sudo nano /home/dsign/dsign/templates/errors/401.html
sudo nano /home/dsign/dsign/templates/errors/403.html
sudo nano /home/dsign/dsign/templates/errors/404.html
sudo nano /home/dsign/dsign/templates/errors/500.html

Стили CSS
sudo nano /home/dsign/dsign/static/css/base.css
sudo nano /home/dsign/dsign/static/css/gallery.css
sudo nano /home/dsign/dsign/static/css/index.css
sudo nano /home/dsign/dsign/static/css/login.css
sudo nano /home/dsign/dsign/static/css/playlist.css
sudo nano /home/dsign/dsign/static/css/settings.css

Дать доступ только пользователю dsign:
sudo chown dsign:dsign /var/log/dsign.log
sudo chmod 644 /var/log/dsign.log
sudo chown dsign:video /dev/tty1
sudo chmod 0660 /dev/tty1
sudo chown dsign:dsign /var/lib/dsign/settings.json
sudo chmod 660 /var/lib/dsign/settings.json
sudo chown -R www-data:www-data /var/lib/dsign/media #Веб интерфеейс, доступ
sudo chmod -R 777 /var/lib/dsign/media
sudo usermod -aG video www-data  # Добавить пользователя в группу video
sudo usermod -aG video dsign

sudo touch /etc/dsign.conf
sudo chown dsign:dsign /etc/dsign.conf

Проверка прав
ls -l /var/log/dsign.log

Проверка наличия
ls -la /usr/local/bin/dsign-capture
ls -la /home/dsign/dsign/static/images/

sudo chmod +x /usr/local/bin/dsign-capture
sudo chmod +x /usr/local/bin/cleanup-dsign.sh
sudo chown -R dsign:www-data /home/dsign/dsign/static/images
sudo chmod -R 775 /home/dsign/dsign/static/images
sudo chown -R dsign:www-data /var/lib/dsign/media
sudo chmod -R 775 /var/lib/dsign/media/
sudo chmod +x /usr/local/bin/dsign-mpv-manager
sudo chown -R www-data:www-data /home/dsign/dsign/static/images
sudo chmod -R 775 /home/dsign/dsign/static/images
sudo chown -R dsign:www-data /var/lib/dsign/media
sudo chmod -R 775 /var/lib/dsign/media
sudo chown -R dsign:www-data /var/lib/dsign/media/thumbnails



Настройте права на устройства DRM:
sudo tee /etc/udev/rules.d/10-drm.rules > /dev/null <<EOL
KERNEL=="card[0-9]*", GROUP="video", MODE="0660"
KERNEL=="renderD[0-9]*", GROUP="video", MODE="0660"
EOL

# Добавляем пользователя в группу video
sudo usermod -aG video dsign

# Проверяем права на устройство
sudo chmod 660 /dev/dri/card0
sudo chown root:video /dev/dri/card0

sudo udevadm control --reload-rules
sudo udevadm trigger

Полезные опции
Имя приставки
Адрес приставки
Локация/Расположение
Назначение
Ответственный

ip a #Найдите имя Wi-Fi интерфейса
sudo raspi-config #Настройка Wi-Fi через raspi-config
sudo nano /etc/wpa_supplicant/wpa_supplicant.conf #Ручная настройка Wi-Fi через wpa_supplicant

network={
    ssid="ИМЯ_ВАШЕЙ_WIFI_СЕТИ"
    psk="ВАШ_WIFI_ПАРОЛЬ"
}

network={
    ssid="MyHiddenWiFiNetwork"
    scan_ssid=1 #Если скрытая сеть
    psk="MySecurePassword123"
}

sudo wpa_cli -i wlan0 reconfigure #Перезапустите Wi-Fi интерфейс, чтобы применить изменения
ip a show wlan0 #Проверка подключения

mkdir -p /home/dsign/"Название папки"
mkdir -p static/css
rm путь и название файла #Удаление

#Копирование скриншота в галлерею
sudo cp -f /home/dsign/dsign/static/images/on_air_screen.jpg /var/lib/dsign/media/on_air_screen.jpg && sudo chown dsign:dsign /var/lib/dsign/media/on_air_screen.jpg

Используем kmsgrab (часть ffmpeg) для захвата с DRM
ffmpeg -devices | grep kmsgrab #Проверка наличия kms
sudo apt install ffmpeg libdrm2 #поддержка DRM
chmod +x /usr/local/bin/screenshot.sh #дать права на доступ к скрипту

MPV
sudo pkill -9 mpv || true
pgrep -a mpv || echo "No MPV processes found"

convert -size 800x600 xc:gray ~/dsign/static/images/placeholder.jpg  # Создаёт серое изображение

# Добавить пользователя dsign в группу video
sudo usermod -aG video dsign

# Дать права на чтение фреймбуфера
echo 'SUBSYSTEM=="graphics", KERNEL=="fb[0-9]*", GROUP="video", MODE="0660"' | sudo tee /etc/udev/rules.d/99-fbdev.rules
sudo udevadm control --reload-rules
sudo udevadm trigger


sudo systemctl enable screenshot.timer  # Включить автозагрузку таймера
sudo systemctl start screenshot.timer   # Запустить таймер немедленно

sudo chmod +x /usr/local/bin/dsign-mpv-manager
sudo chown dsign:dsign /usr/local/bin/dsign-mpv-manager
sudo touch /var/log/dsign-mpv-manager.log
sudo chown dsign:dsign /var/log/dsign-mpv-manager.log

MPV Socket
sudo mkdir -p /var/lib/dsign/mpv
sudo chown dsign:dsign /var/lib/dsign/mpv
sudo chmod 775 /var/lib/dsign/mpv


sudo mkdir -p /var/lib/dsign/media/thumbnails
sudo chown -R www-data:www-data /var/lib/dsign/media/thumbnails  # Для Apache/Nginx
sudo chmod -R 775 /var/lib/dsign/media/thumbnails

Проверить синтаксис
python3 -m py_compile /home/dsign/dsign/services/playback_service.py


Сборка MPV Более правильная
sudo apt remove mpv
sudo apt install -y liblua5.2-dev yasm python3-docutils libfreetype6-dev libfribidi-dev libass-dev libjpeg-dev liblcms2-dev libdvdnav-dev

Установка зависимостей
sudo apt install -y \
  git meson ninja-build \
  liblua5.2-dev yasm python3-docutils \
  libfreetype6-dev libfribidi-dev \
  libass-dev libjpeg-dev liblcms2-dev \
  libdvdnav-dev libegl1-mesa-dev \
  libgbm-dev libdrm-dev libxkbcommon-dev \
  libwayland-dev wayland-protocols

Сборка и установка  
git clone https://github.com/mpv-player/mpv.git
cd mpv

meson setup build
meson configure build -Dlibmpv=true -Dlua=enabled

ninja -C build
sudo ninja -C build install

Обнови кэш библиотек:
sudo ldconfig

Проверь версию:
mpv --version

Выполни эту команду перед meson setup:
sudo apt install -y libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libavfilter-dev libavdevice-dev
sudo apt install -y libfreetype6-dev libass-dev libjpeg-dev libdrm-dev libxkbcommon-dev liblua5.2-dev libv4l-dev

Обновление Meson
sudo apt remove meson -y
pip3 install --user meson
После установки добавь pip-бинарники в PATH, если ещё не добавлено:
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
Проверь:
meson --version

После этого снова запусти сборку:
cd ~/mpv
rm -rf mpv
git clone --branch v0.35.1 https://github.com/mpv-player/mpv.git
cd mpv
meson setup build
meson configure build -Dlibmpv=true -Dlua=enabled
ninja -C build
sudo ninja -C build install

pip3 uninstall -y meson
pip3 install --user meson
# убедись, что путь к ~/.local/bin в PATH
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc

# теперь проверь
which meson
meson --version

После успешной переустановки meson:
cd ~/mpv/mpv
rm -rf build  # удаляем старый build
meson setup build
meson configure build -Dlibmpv=true -Dlua=enabled
ninja -C build
sudo ninja -C build install


Прилагаю репозиторий для подробного анализа
https://github.com/kirpodolak/dsign/tree/main/dsign
https://github.com/kirpodolak/dsign/tree/main/dsign/config
https://github.com/kirpodolak/dsign/tree/main/dsign/routes
https://github.com/kirpodolak/dsign/tree/main/dsign/routes/api
https://github.com/kirpodolak/dsign/tree/main/dsign/services
https://github.com/kirpodolak/dsign/tree/main/dsign/static
https://github.com/kirpodolak/dsign/tree/main/dsign/static/css
https://github.com/kirpodolak/dsign/tree/main/dsign/static/images
https://github.com/kirpodolak/dsign/tree/main/dsign/static/js
https://github.com/kirpodolak/dsign/tree/main/dsign/static/js/utils
https://github.com/kirpodolak/dsign/tree/main/dsign/templates
https://github.com/kirpodolak/dsign/tree/main/dsign/services/sockets
https://github.com/kirpodolak/dsign/tree/main/dsign/services/sockets/handlers

Utils JS
Конвертируй в ES Module, внеси нужные правки и выведи полный код без пропусков.

Подключение к WiFI
sudo nmcli dev wifi connect "ИМЯ_СЕТИ" password "ВАШ_ПАРОЛЬ" hidden yes
# 1. Проверяем доступные интерфейсы
nmcli dev status
# 2. Подключаемся к скрытой сети (основная команда)
sudo nmcli dev wifi connect "ИМЯ_ВАШЕЙ_СЕТИ" password "ВАШ_ПАРОЛЬ" hidden yes
# 3. Проверяем статус подключения
nmcli connection show
# 4. Проверяем статус устройств
nmcli dev status
# 5. Проверяем IP адрес
hostname -I
# 6. Проверяем интернет
ping -c 3 google.com


Миграция с нуля

1. Установка системных зависимостей
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    python3-full \
    python3-pip \
    python3-venv \
    git \
    mpv \
    sqlite3 \
    scrot \
    imagemagick \
    nginx \
    python3-dev \
    build-essential
	
# Создаем системные папки
sudo mkdir -p /var/lib/dsign/media
sudo mkdir -p /var/log/dsign
sudo mkdir -p /usr/local/bin
sudo mkdir -p /home/dsign

# Создаем пользователя dsign (если нет)
sudo useradd -m -d /home/dsign -s /bin/bash dsign || true

# Назначаем права
sudo chown -R dsign:dsign /var/lib/dsign
sudo chown -R dsign:dsign /var/log/dsign
sudo chown -R dsign:dsign /home/dsign
sudo chmod 755 /var/lib/dsign/media
# Добавляем пользователя во все нужные группы
sudo usermod -a -G video,render,input,audio,tty dsign

# Переключаемся на пользователя dsign
sudo su - dsign

# Клонируем репозиторий
git clone https://github.com/kirpodolak/dsign.git /home/dsign/dsign-project
cd /home/dsign

# Создаем правильную структуру из репозитория
cp -r dsign-project/dsign/* dsign/
cp dsign-project/requirements.txt ./

# Создаем виртуальное окружение
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

Команда удаления папки
rm -rf dsign


Samba - общие папки как в Windows
# Устанавливаем Samba
sudo apt update && sudo apt install -y samba samba-common-bin
# Настраиваем общую папку
sudo nano /etc/samba/smb.conf
Добавьте в конец файла:

ini
[dsign]
path = /home/dsign
available = yes
valid users = dsign
read only = no
browseable = yes
public = yes
writable = yes

# Устанавливаем пароль Samba для пользователя dsign
sudo smbpasswd -a dsign
# Перезапускаем службу
sudo systemctl restart smbd
sudo systemctl enable smbd
В Windows:
Откройте \\IP_АДРЕС_RASPBERRY_PI\ в проводнике
Или в адресной строке: \\192.168.1.100\dsign

Поиск причины произвольного ребута
sudo journalctl -b -1 -p err..alert --no-pager
sudo journalctl -b -1 -k --no-pager | tail -n 300
sudo journalctl -b -1 --no-pager | egrep -i 'watchdog|reset|brownout|undervoltage|thrott|mmc|I/O error|ext4|oom|panic' | tail -n 200

sudo journalctl --list-boots --no-pager | head -n 5
sudo journalctl -b -1 -p err..alert --no-pager
sudo journalctl -b -1 -k --no-pager | tail -n 300
sudo journalctl -b -1 --no-pager | egrep -i 'watchdog|reset|brownout|undervoltage|thrott|mmc|i/o error|ext4|oom|panic' | tail -n 200
vcgencmd get_throttled
vcgencmd measure_temp

Сбои MPV логи
sudo systemctl status dsign-mpv.service --no-pager
sudo ls -lah /var/log/dsign/
sudo tail -n 200 /var/log/dsign/mpv.log
rg -n "ytdl|ytdl_hook|yt-dlp|youtube-dl" /var/log/dsign/mpv.log


Поиск сбоев по ВК
Option A (simplest): force ripgrep to treat it as text
sudo rg -a -n -S "(vkvideo\.ru|vk\.com/video|okcdn\.ru|\[ytdl_hook\]|Starting subprocess: \[yt-dlp|HTTP error|403|400|forbidden|denied|TLS|EOF|on_after_end_file|Opening done)" /var/log/dsign/mpv.log | tail -n 250
Option B (most robust): strip NULs, then search
sudo tr -d '\000' < /var/log/dsign/mpv.log \
  | rg -n -S "(vkvideo\.ru|vk\.com/video|okcdn\.ru|\[ytdl_hook\]|Starting subprocess: \[yt-dlp|HTTP error|403|400|forbidden|denied|TLS|EOF|on_after_end_file|Opening done)" \
  | tail -n 250
Option C: show just the VK attempt window (starting from the loadfile line)
sudo tr -d '\000' < /var/log/dsign/mpv.log \
  | rg -n -S "loadfile.*ytdl://https://vkvideo\.ru/|Starting subprocess: \[yt-dlp|\[ytdl_hook\]|okcdn\.ru|Opening done|on_after_end_file|HTTP error|403|400|EOF" \
  | tail -n 300
Option D: if you want only VK/OKCDN lines (very low noise)
sudo tr -d '\000' < /var/log/dsign/mpv.log \
  | rg -n -S "(vkvideo\.ru|vk\.com/video|okcdn\.ru)" \
  | tail -n 250
Journald side (often cleaner than mpv.log)
sudo journalctl -u dsign-mpv.service -S "$(cat /tmp/vk_test_started_at.txt)" --no-pager \
  | rg -n -S "(vkvideo\.ru|vk\.com/video|okcdn\.ru|ytdl_hook|yt-dlp|HTTP error|403|400|Opening done|on_after_end_file|EOF)" \
  | tail -n 250

Поиск сбоев по Рутуб
Очистить лог mpv и воспроизвести один Rutube-ролик
sudo truncate -s 0 /var/log/dsign/mpv.log
date -Is | tee /tmp/rutube_test_started_at.txt
# запусти Rutube из UI и подожди 15-30 секунд
Вытащить из mpv.log только важные строки вокруг ошибки
sudo rg -a -n -S "(rutube\.ru|rtbcdn\.ru|river-|bl\.rutube\.ru|ytdl_hook|Starting subprocess: \[yt-dlp|streamurl:|stream-open-filename|file-local-options/stream-lavf-o|http-header-fields|HTTP error 400|HTTP error 403|forbidden|denied|TLS|SSL|handshake|EOF|Opening done)" /var/log/dsign/mpv.log | tail -n 350
Сервисные логи (и Flask, и mpv) за попытку запуска
since="$(cat /tmp/rutube_test_started_at.txt)"
sudo journalctl -u digital-signage.service -u dsign-mpv.service --since "$since" --no-pager | rg -n "rutube|rtbcdn|river-|HTTP error|ytdl|lavf|stream-lavf|header|cookie|error|warn" | tail -n 250
sudo rg -a -n "Set property: (http-header-fields|file-local-options/stream-lavf-o)=|Opening https://river-|HTTP error 400" /var/log/dsign/mpv.log | tail -n 120
sudo rg -a -n "Opening https://river-.*rutube\.ru/.*\.m3u8|HTTP error 400 Bad Request" /var/log/dsign/mpv.log | tail -n 30
yt-dlp --no-warnings -J --no-playlist "https://rutube.ru/video/024cd372464440d01bae7f296935a06d/?r=plwd" > /tmp/rutube.json

python3 - <<'PY'
import json
d=json.load(open("/tmp/rutube.json","r",encoding="utf-8"))
print("UA:", (d.get("http_headers") or {}).get("User-Agent"))
fmts=d.get("formats") or []
# покажем 5 первых river- URL + длину cookies
c=0
for f in fmts:
    if not isinstance(f,dict): continue
    url=str(f.get("url") or "")
    if "river-" in url and url.endswith(".m3u8") or (".m3u8?" in url and "river-" in url):
        cookies=f.get("cookies") or ""
        print("URL:", url[:140])
        print("cookies_len:", len(cookies))
        c+=1
        if c>=5: break
PY

Проверить, что mpv не добавляет “плохие” заголовки (Referer/Origin) перед river
sudo rg -a -n "Set property: http-header-fields=|Set property: file-local-options/stream-lavf-o=|Opening https://river-|HTTP error 400" /var/log/dsign/mpv.log | tail -n 200
Проверить системное время (anti-bot часто чувствителен
date -Is
timedatectl status | rg -n "System clock synchronized|NTP service|Time zone|Local time|Universal time"
Проверить, что перед Opening https://river-... появился Cookie (и что 400 ушёл)
sudo rg -a -n "Set property: http-header-fields=|Set property: file-local-options/stream-lavf-o=|Opening https://river-|HTTP error 400" /var/log/dsign/mpv.log | tail -n 220
sudo rg -a -n "stream-lavf-o=\\{|http_version|Opening https://river-|HTTP error 400" /var/log/dsign/mpv.log | tail -n 160
