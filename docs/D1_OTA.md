# D1 — OTA self-update (`dsign-update`)

**Версия:** 2026-07-10  
**Backlog:** `D1` · зависит от **D0** (`dsign-apply-install`)

Fleet-обновление без ручного `git pull`: git fetch/merge → `pip install` → `dsign-apply-install` → restart systemd.

---

## Команды

```bash
sudo dsign-update check      # git fetch; exit 1 если есть обновление
sudo dsign-update download   # ff-only merge + сохранить rollback point
sudo dsign-update apply      # pip + manifest apply + restart services
sudo dsign-update rollback   # git reset --hard к rollback + apply
sudo dsign-update status     # state.json + rollback.json
sudo dsign-update auto       # check → download → apply (для timer)
```

JSON: добавьте `--json` к любой команде.

---

## Что делает `apply`

1. `pip install -r requirements.txt` (или `pip install -e .`) в `DSIGN_VENV`
2. **`dsign-apply-install -q`** — не только git, а полный manifest D0
3. `systemctl restart` signage stack:
   - **drm:** `digital-signage.service`, `dsign-mpv.service`
   - **wayland:** + `dsign-compositor`, `dsign-logo`, `dsign-mpv-wayland`

Rollback point сохраняется в **`download`** (commit до merge).

---

## Конфигурация

| Файл | Назначение |
|------|------------|
| `/etc/dsign/ota.env` | Ветка, enable/disable (шаблон: `etc/dsign/ota.env.example`) |
| `/var/lib/dsign/ota/state.json` | Последний check/apply |
| `/var/lib/dsign/ota/rollback.json` | Commit для `rollback` |

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_OTA_ENABLED` | `1` | `0` — `auto` no-op |
| `DSIGN_OTA_BRANCH` | `main` | Ветка для fetch/merge |
| `DSIGN_OTA_REMOTE` | `origin` | Git remote |
| `DSIGN_PROJECT_ROOT` | `/home/dsign/dsign` | Git repo |
| `DSIGN_VENV` | `/home/dsign/venv` | venv для pip |
| `DSIGN_DISPLAY_BACKEND` | `drm` | Какие units рестартить |

---

## systemd timer (03:00)

```bash
sudo systemctl enable --now dsign-update.timer
systemctl list-timers dsign-update.timer
```

Ручной прогон:

```bash
sudo systemctl start dsign-update.service
journalctl -u dsign-update.service -b --no-pager
```

Отключить OTA на плеере:

```bash
echo 'DSIGN_OTA_ENABLED=0' | sudo tee /etc/dsign/ota.env
sudo systemctl disable --now dsign-update.timer
```

---

## Acceptance (backlog)

| Критерий | Как проверить |
|----------|----------------|
| Downtime < 5 мин | `time sudo dsign-update apply` после `download` |
| Rollback < 2 мин | `time sudo dsign-update rollback` |
| Fail не ломает систему | прервать apply → `rollback`; signage должен подняться |

---

## Первичная настройка на Pi

```bash
# После deploy ветки с D1:
sudo dsign-apply-install -q
sudo cp /home/dsign/dsign/etc/dsign/ota.env.example /etc/dsign/ota.env
# отредактировать при необходимости
sudo systemctl daemon-reload
sudo systemctl enable --now dsign-update.timer

# smoke:
sudo dsign-update check --json | jq .
sudo dsign-update status --json | jq .
```

---

## Автотесты

`dsign/tests/test_ota_update.py` — mock git/pip/systemctl (без сети на CI).

---

## См. также

- [deploy-manifest.yaml](./deploy-manifest.yaml) — D0 apply
- [dsign_backlog.md](./dsign_backlog.md) §D1
- [ENVIRONMENT.md](./ENVIRONMENT.md)
