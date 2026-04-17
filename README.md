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
sudo sed -i 's/\r$//' /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip
sudo chmod 755 /usr/local/bin/dsign-network-assistant /usr/local/bin/dsign-show-startup-ip
```

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
