# DSign

Digital Signage система на базе Flask + MPV для Raspberry Pi/Linux:

- управление медиа, плейлистами и настройками через web UI;
- аппаратный вывод контента через `mpv` (DRM/TTY);
- запуск как systemd-сервисов;
- экранный startup assistant для первичного подключения к сети;
- показ стартового IP на экране контента.

---

## Ключевые возможности

- Web UI для управления плейлистами, галереей и профилями.
- API + Socket.IO для статусов и realtime-обновлений.
- Воспроизведение через MPV IPC (`/var/lib/dsign/mpv/socket`).
- Idle logo режим при отсутствии активного плейлиста.
- Startup network onboarding:
  - если сеть недоступна, открывается интерактивный `nmtui` на `tty1`;
  - после подключения сохраняется текущий IP;
  - IP кратко выводится поверх контента через MPV OSD.

---

## Архитектура запуска (systemd)

Проект рассчитан на работу как набор сервисов:

1. `dsign-network-assistant.service` (oneshot, root)
  - выполняется до mpv/web;
  - проверяет сеть;
  - при необходимости запускает интерактивный выбор Wi-Fi (`nmtui-connect`) на `tty1`;
  - сохраняет IP в `/tmp/dsign-startup-ip.txt`.
2. `dsign-mpv.service` (long-running, user `dsign`)
  - поднимает DRM вывод на `tty1`;
  - запускает mpv с IPC сокетом;
  - после старта триггерит `dsign-show-startup-ip.service` (best-effort).
3. `dsign-show-startup-ip.service` (oneshot, user `dsign`)
  - ждет готовности mpv сокета и startup IP файла;
  - отправляет MPV команду `show-text` на 60 секунд;
  - удаляет startup IP файл.
4. `digital-signage.service` (Flask backend)
  - поднимает API/web UI;
  - использует `PlaybackService`/`SocketService`.

Важно: network assistant подключен через `Wants`, а не через жесткие блокирующие зависимости, чтобы отказ helper не валил весь стек.

---

## Репозиторий (кратко)

- `dsign/` — основной Python пакет приложения.
  - `routes/` — web/API маршруты.
  - `services/` — playback/network/socket бизнес-логика.
  - `static/` и `templates/` — frontend.
- `etc/systemd/system/` — эталонные unit-файлы.
- `usr/local/bin/` — helper скрипты для systemd/экранного запуска.
- `install_dsign.sh` — bootstrap/provisioning скрипт для чистой системы.
- `run.py` — точка входа.

---

## Быстрый запуск для разработки

```bash
git clone https://github.com/kirpodolak/dsign.git
cd dsign
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

Web UI: `http://localhost:5000`

---

## Установка на Raspberry Pi (production)

Для чистой системы используется `install_dsign.sh` (bootstrap):

- устанавливает зависимости;
- раскладывает unit-файлы;
- ставит helper-скрипты;
- включает и запускает сервисы.

Если система уже развернута, обычно достаточно точечного обновления файлов + `systemctl daemon-reload` + restart сервисов.

---

## Полезные команды диагностики

### Статус сервисов

```bash
systemctl status dsign-network-assistant.service --no-pager -l
systemctl status dsign-mpv.service --no-pager -l
systemctl status dsign-show-startup-ip.service --no-pager -l
systemctl status digital-signage.service --no-pager -l
```

### Логи

```bash
journalctl -u dsign-network-assistant.service -b --no-pager -n 100
journalctl -u dsign-mpv.service -b --no-pager -n 100
journalctl -u dsign-show-startup-ip.service -b --no-pager -n 100
journalctl -t dsign-show-startup-ip -b --no-pager -n 100
```

### Проверка фактического unit-конфига

```bash
systemctl cat dsign-mpv.service
systemctl cat dsign-network-assistant.service
```

---

## Частые проблемы и решения

### `env: 'bash\r': No such file or directory`

Причина: CRLF в shell-скрипте.

Фикс:

```bash
sudo sed -i 's/\r$//' /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip /usr/local/bin/dsign-wifi-on-display
sudo chmod 755 /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip /usr/local/bin/dsign-wifi-on-display
```


### Кнопки в nmtui неактивны (сети видны, но Enter/стрелки не работают)

**DRM (tty):** `nmtui` запускался без **controlling TTY**. MPV держит tty1, клавиатура не доходит до nmtui. Фикс: Wi-Fi UI на **vt2** через `openvt` (`dsign-nmtui-tty`).

**Wayland (labwc + foot):** клавиатура может оставаться у mpv, если `foot` открыт с `--maximized` поверх fullscreen mpv (labwc #2534). Фикс: `foot -W` (без maximize), `wlrctl toplevel focus app_id:dsign-wifi`, mpv `--input-vo-keyboard=no`. Пакеты: `foot`, `wlrctl`, `wtype`.

Проверка на устройстве:

```bash
# /usr/local may be mounted noexec — invoke via bash if you see "Permission denied"
# After manual copy from Windows, strip CRLF or bash fails with "set: invalid option" / $'\r'
sudo sed -i 's/\r$//' /usr/local/bin/dsign-diagnose-wifi-on-display /usr/local/bin/dsign-*
sudo bash /usr/local/bin/dsign-diagnose-wifi-on-display
command -v openvt   # DRM stack; on Wayland: command -v foot
```

После деплоя:

```bash
sudo cp usr/local/bin/dsign-nmtui-tty /usr/local/bin/
sudo chmod 755 /usr/local/bin/dsign-nmtui-tty
sudo systemctl restart dsign-mpv.service
```

### `dsign-wifi-on-display` не запускается

Скрипт вызывается из `dsign-show-startup-ip` от пользователя `dsign` через `sudo -n` (без пароля). Нужны **файл**, **chmod** и **sudoers**.

На устройстве после `git pull`:

```bash
cd /home/dsign/dsign

sudo cp usr/local/bin/dsign-wifi-on-display /usr/local/bin/
sudo cp usr/local/bin/dsign-show-startup-ip /usr/local/bin/
sudo chmod 755 /usr/local/bin/dsign-wifi-on-display /usr/local/bin/dsign-show-startup-ip
sudo sed -i 's/\r$//' /usr/local/bin/dsign-wifi-on-display /usr/local/bin/dsign-show-startup-ip

sudo cp etc/sudoers.d/dsign-systemctl /etc/sudoers.d/
sudo visudo -cf /etc/sudoers.d/dsign-systemctl

# Проверка от имени dsign (должно отработать без запроса пароля):
sudo -u dsign sudo -n /usr/local/bin/dsign-wifi-on-display
journalctl -t dsign-show-startup-ip -t dsign-wifi-on-display -b --no-pager -n 50
```

В `/etc/sudoers.d/dsign-systemctl` должна быть строка:

```
dsign ALL=(root) NOPASSWD: /usr/local/bin/dsign-wifi-on-display
```

Отдельный `dsign-wifi-on-display.service` не используется — не создавайте его вручную.

### MPV стартует, но IP OSD не появляется

Проверить:

- есть ли `/tmp/dsign-startup-ip.txt`;
- установлен ли `socat`;
- статус `dsign-show-startup-ip.service` и его журналы.

### `status=127` в `ExecStartPost`

В текущей схеме post-стадия best-effort и не должна валить `dsign-mpv.service`.
Если видите старое поведение — перепроверьте, что применен актуальный `dsign-mpv.service`.

### Нет `rg` на устройстве

На Pi может не быть `ripgrep`. Используйте `grep`/`journalctl`/`systemctl cat`.

---

## О роли `install_dsign.sh`

`install_dsign.sh` нужен в первую очередь для **первичного разворачивания** на чистой ОС.
Он тоже поддерживается в актуальном состоянии, чтобы новые устройства сразу получали все последние фиксы (сервисы, helper-скрипты, зависимости).

---

## Лицензия

MIT. См. `LICENSE`.
