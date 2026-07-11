# D1 — OTA self-update (`dsign-update`)

**Версия:** 2026-07-10  
**Backlog:** `D1` · зависит от **D0** (`dsign-apply-install`)

Fleet-обновление без ручного `git pull`: git fetch/merge → `pip install` → `dsign-apply-install` → restart systemd.

---

## Troubleshooting

### `not a git repository`

OTA needs a **git clone** (not a plain file copy). On the player:

```bash
ls -la /home/dsign/dsign/.git
# or
ls -la /home/dsign/dsign-new/.git
```

Set in `/etc/dsign/ota.env`:

```bash
DSIGN_PROJECT_ROOT=/home/dsign/dsign   # path that contains .git/
```

### `jq: parse error` / `version` unknown

**Symptoms:** `fatal: invalid reference: cursor/d1-ota-8ed1`, `installed from git checkout`, or
`jq: parse error` while `ota_update.py` is missing under `dsign-new`.

**Common Pi layout bug:** module only under `/home/dsign/dsign/services/ota_update.py` (prod flat path),
but `DSIGN_PROJECT_ROOT=/home/dsign/dsign-new`. Wrapper must load module from **project root only**.

Cause: **stale** `/tmp/dsign-ota-bootstrap` (old script used `git checkout`, not `git show FETCH_HEAD`).
Re-download every time — do not reuse an old `/tmp` copy.

```bash
RAW=https://raw.githubusercontent.com/kirpodolak/dsign/cursor/d1-ota-8ed1

# Option A — bootstrap (recommended; must curl fresh copy)
sudo curl -fsSL "$RAW/usr/local/bin/dsign-ota-bootstrap" -o /tmp/dsign-ota-bootstrap
grep -q 'BOOTSTRAP_VERSION=' /tmp/dsign-ota-bootstrap || { echo "stale bootstrap"; exit 1; }
DSIGN_OTA_BOOTSTRAP_PREFER=curl sudo bash /tmp/dsign-ota-bootstrap
# expect: version=2026-07-10-fetchhead-v4

# Option B — manual fix (module + git ownership)
sudo mkdir -p /home/dsign/dsign-new/dsign/services /home/dsign/dsign-new/services
sudo curl -fsSL "$RAW/dsign/services/ota_update.py" -o /home/dsign/dsign-new/dsign/services/ota_update.py
sudo curl -fsSL "$RAW/usr/local/bin/dsign-update" -o /usr/local/bin/dsign-update
sudo chmod 755 /usr/local/bin/dsign-update
sudo chown -R dsign:dsign /home/dsign/dsign-new/.git /home/dsign/dsign-new

sudo dsign-update version --json | jq .tool_version
# expect: "2026-07-10-pi6"
```

### `jq: parse error` with module present in `dsign-new`

If `ota_update.py` exists but `version --json` prints plain `2026-07-10-pi5` (no `{`), update to **pi6+**
(`--json` was ignored when run via `python ota_update.py` — fixed in pi6).

```bash
RAW=https://raw.githubusercontent.com/kirpodolak/dsign/cursor/d1-ota-8ed1
sudo curl -fsSL "$RAW/dsign/services/ota_update.py" -o /home/dsign/dsign-new/dsign/services/ota_update.py
sudo chown dsign:dsign /home/dsign/dsign-new/dsign/services/ota_update.py
sudo dsign-update version --json | jq .tool_version   # "2026-07-10-pi6"
```

### `insufficient permission for adding an object to repository database .git/objects`

OTA runs `git fetch` as user `dsign` (`sudo -u dsign`). If `.git/` was touched by `root` (bootstrap, manual sudo git), fetch fails.

```bash
sudo chown -R dsign:dsign /home/dsign/dsign-new/.git
sudo chown -R dsign:dsign /home/dsign/dsign-new
sudo dsign-update check --json | jq .
```

### `working tree has local changes`

Bootstrap installs `ota_update.py` into the clone before D1 is on `main`. **pi6+** ignores only those paths for `download`/`auto`; other local edits still block OTA.

```bash
cd /home/dsign/dsign-new && git status --short
# ?? dsign/services/ota_update.py  ?? services/ota_update.py  — OK on pi6+
sudo dsign-update download --json | jq .
```

`/etc/dsign/ota.env`:

```bash
DSIGN_PROJECT_ROOT=/home/dsign/dsign-new
```

---

```bash
sudo dsign-update check      # git fetch; exit 1 if update available
sudo dsign-update download   # ff-only merge + save rollback point
sudo dsign-update apply      # pip + manifest apply + restart services
sudo dsign-update rollback   # git reset --hard + apply
sudo dsign-update status     # state.json + rollback.json
sudo dsign-update auto       # check → download → apply (timer)

sudo dsign-update check --json | jq .    # preferred JSON
sudo dsign-update --json check | jq .    # JSON flag before subcommand
```

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
