[dsign_4phase_checklist.md](https://github.com/user-attachments/files/29330658/dsign_4phase_checklist.md)
# DSign — План развития: от хорошего плеера к референсному

**Порядок фаз:** A → C → B → D (Playback → Content → API → Ops)  
**Формат задач:** Что меняем → Зачем → Какой результат  
**Связанные документы:** [dsign_test_matrix.md](./dsign_test_matrix.md) — acceptance-тесты playback

> Живой чеклист. При закрытии задачи: `[ ]` → `[x]`, указать PR/коммит в колонке «Где сделано».

---

## Сводка прогресса (обновлять при каждом PR)

| ID | Задача | Статус | Где сделано |
|----|--------|--------|-------------|
| **D0** | Манифест деплоя + verify/apply | ✅ сделано | main, PR #82 |
| **A0** | Сетевой playback / hung / ytdl resilience | ✅ сделано | main, PR #80 |
| **A1** | MPV internal playlist (локальное видео) | ✅ сделано | main, PR #85 |
| **A2** | Single video `loop-file=inf` | ✅ сделано | main, PR #85 |
| **A3** | Safe loadfile + ffprobe | 🟡 в PR | `playlist_management.py` |
| **A4** | `video-sync=audio` Wayland | 🟡 в репо | `etc/…/intel-iris-xe-balanced-wayland.conf`; на плеере — сверить D0 |
| **A5** | Tech debt | ✅ сделано | PR A5 |
| **C1** | ContentCache | ⬜ не начато | — |
| **C2** | Audio-only + logo | ⬜ не начато | — |
| **C3** | Nested playlists | ⬜ не начато | — |
| **B1** | Расширить `/api/playback/status` | 🟡 частично | `network_health` ✅; остальные поля ⬜ |
| **B2** | `GET /api/health` | 🟡 частично | `health_check()` в коде ✅; REST endpoint ⬜ |
| **B3** | `POST /api/playback/override` | ⬜ не начато | — |
| **B4** | API Bearer token | ⬜ не начато | — |
| **B5** | Remote control (REST, не WS stub) | ⬜ не начато | WS = `pass` |
| **D1** | `dsign-update` OTA | ⬜ не начато | зависит от **D0** |
| **D2** | Local schedule (SQLite) | ⬜ не начато | после B3 |

**Легенда:** ✅ сделано · 🟡 частично · ⬜ не начато

---

## Рекомендуемый порядок реализации

Продуктовый порядок из плана сохраняем (**A → C → B → D**), но **D0** и **A5** — до масштабных изменений playback и fleet.

```mermaid
flowchart LR
  D0[D0 manifest + verify] --> A5[A5 tech debt]
  A5 --> A2[A2 loop-file inf]
  A2 --> A1[A1 mpv playlist]
  A1 --> A3[A3 safe loadfile]
  A3 --> A4[A4 video-sync deploy]
  A4 --> C[C1-C3]
  C --> B[B1-B5]
  D0 --> D1[D1 OTA]
  B --> D2[D2 schedule]
```

| Шаг | PR-фокус | Зачем сейчас |
|-----|----------|--------------|
| 1 | **D0** | Сверка «репо vs плеер» — без этого непонятно, что залито в `/etc`, `/usr`, `/var` |
| 2 | **A5** | Разблокировать чистый Wayland-деплой и `get_status()` |
| 3 | **A2 → A1** | Zero-gap на локальном видео (главная цель фазы A) |
| 4 | **A3 → A4** | Устойчивость к битым файлам + judder на Wayland |
| 5 | **C** | Offline / форматы |
| 6 | **B** | Remote control для пилота интеграторов |
| 7 | **D1 → D2** | OTA и расписание (D1 только поверх D0) |

---

## Деплой: проблема сверки (почему нужен D0)

Сейчас обновление **не равно** «всё из репо на плеере».

| Слой | Путь на плеере | Как обычно обновляют |
|------|----------------|----------------------|
| Python-приложение | `/home/dsign/dsign/` | `git pull` |
| Helper-скрипты | `/usr/local/bin/dsign-*` | вручную `sudo cp` |
| systemd units | `/etc/systemd/system/` | часть из `etc/`, часть **heredoc** в `install_dsign.sh` |
| Конфиги mpv/labwc | `/var/lib/dsign/mpv-minimal/`, `labwc/`, `config/` | частично, часто `if-not-exists` |
| sudoers | `/etc/sudoers.d/` | вручную, не все файлы из репо в install |

**Примеры рассинхрона (проверять при D0):**

- В репо есть `dsign-preview-timer`, `dsign-capture`, `dsign-diagnose-wifi-on-display` — в `install_dsign.sh` не все ставятся.
- `etc/sudoers.d/dsign-screenshot` — в install не попадает.
- `dsign-mpv.service` на устройстве может отличаться от `etc/systemd/system/dsign-mpv.service` (генерация heredoc).

**D0 не заменяет D1 (OTA)** — D0 отвечает на вопрос: *«что на плеере vs что в репо»*. D1 — *«как автоматически применить новую версию»*.

---

## Фаза D0 — Deploy manifest & verify (добавлено к плану)

**Цель:** Одна команда показывает drift; вторая — догоняет систему до репо.

### D0.1. `docs/deploy-manifest.yaml`

- [ ] Каждая запись: `src` (в репо) → `dest` (на системе), `mode` (`always` / `if-missing` / `never`), `post` (`daemon-reload`, `restart unit`)
- [ ] Покрыть: `usr/local/bin/`, `etc/systemd/`, `etc/sudoers.d/`, `etc/dsign/`, `etc/tmpfiles.d/`
- [ ] Пометить файлы, которые сегодня только в heredoc `install_dsign.sh`

### D0.2. `dsign-verify-install`

- [ ] Отчёт: `OK` / `DRIFT` / `MISSING` / `EXTRA_ON_SYSTEM`
- [ ] Режим `--json` для скриптов/monitoring
- [ ] Acceptance: после `git pull` сразу видно, что ещё не залито

### D0.3. `dsign-apply-install`

- [ ] Идемпотентный apply по манифесту (`--only drifted`)
- [ ] `install_dsign.sh` → bootstrap (user, venv, nginx) + вызов apply

**Cursor prompt (D0):**
> Add docs/deploy-manifest.yaml mapping repo paths to system paths. Implement usr/local/bin/dsign-verify-install and dsign-apply-install. Refactor install_dsign.sh to use manifest instead of duplicating heredoc units where etc/systemd files exist.

---

## A0 — Сетевой playback (вне исходного A1–A5, уже в main)

Закрывает инциденты Rutube/VK (hung, 52 мин на logo). **Не заменяет** A1–A2 (zero-gap локали).

| Пункт | Статус | Примечание |
|-------|--------|------------|
| Mid-stream reload / stagnation / proactive refresh | ✅ | `playlist_management.py` |
| `dsign-mpv-recover` — правильный Wayland unit | ✅ | PR #80 |
| Progressive ytdl open timeout (90s / 45s) | ✅ | |
| Abort после N consecutive open failures | ✅ | default N=3 |
| Resume **last-good** после hung | ✅ | `get_resume_start_index_for_hung_recovery()` |
| Full-cycle all-network-fail → last-good или logo+cooldown | ✅ | |
| `network_health` в status / health_check | ✅ | частичный B1/B2 |

**Acceptance (полевой):** после hung recovery — не полный обход плейлиста по 3 мин/ролик; `consecutive_ytdl_failures` в API.

---

## Фаза A — «Ноль чёрных кадров» (Playback Engine)

**Цель:** Плеер не показывает чёрный экран. Любой контент → любой контент без gap.

**Ветвление в `play()` (целевая архитектура):**

```
all local video?     → A1 play_local_video_playlist()
exactly 1 local?     → A2 loop-file=inf
mixed / network / images → _manual_slideshow_loop() (как сейчас)
```

---

### A1. Local Video Playlist → MPV Internal Playlist

**Статус:** ⬜ не начато

**Что меняем:**
- В `PlaylistManager.play()` — ветка `play_local_video_playlist()` если все items — локальные видео.
- M3U/FFconcat + один `loadfile`.
- `prefetch-playlist=yes` в mpv.conf.

**Зачем:** Сейчас каждый переход = `loadfile replace` = 50–500 ms чёрный кадр. MPV internal playlist = switch buffer ≈ 0 ms.

**Логика:**
```
Плеер видит: [video1.mp4, video2.mp4, video3.mp4]
Вместо: loadfile v1 → EOF → loadfile v2 → EOF → …
Делаем: loadfile playlist.m3u → MPV предзагружает следующий
Мониторим: playlist-pos
```

**Результат:** Video→video zero gap; ниже CPU; стабильнее memory.

**Acceptance:**
- [ ] 10 циклов [A, B] — 10/10 zero gap
- [ ] `journalctl` — 1 `loadfile` на весь плейлист

**Cursor prompt:**
> Branch PlaylistManager.play() for all-local-video playlists: temp M3U, single loadfile, monitor playlist-pos, prefetch-playlist=yes. Keep manual loop for mixed/network/images.

---

### A2. Single Video → loop-file=inf

**Статус:** ✅ сделано (PR #85)

---

### A3. Safe Loadfile — валидация перед playback

**Статус:** 🟡 в PR

**Что меняем:** `_safe_loadfile(path)`:
1. `os.path.exists()`
2. `ffprobe -v error` (паттерн есть в `file_service.py`)
3. `loadfile`
4. wait `vo-configured=true` (max 5 s)
5. fail → skip, logo, next item

**Acceptance:**
- [ ] Плейлист [valid, corrupt, valid] — skip corrupt, без restart плеера

---

### A4. video-sync=audio в Wayland-профиле

**Статус:** 🟡 в репо (`video-sync=audio` в `intel-iris-xe-balanced-wayland.conf`); на плеере — **сверить через D0**

**Acceptance:**
- [ ] 29.97 fps @ 60 Hz — нет judder
- [ ] Файл на устройстве: `/var/lib/dsign/mpv-minimal/profiles/intel-iris-xe-balanced-wayland.conf`

---

### A5. Tech Debt (быстрые фиксы)

**Статус:** ✅ сделано

| Пункт | Статус |
|-------|--------|
| `wayland_manger.py` → `wayland_manager.py` | ✅ |
| `restart_mpv()` dead code в `playlist_management.py` | ✅ удалён |
| `_log_debug` в `playback_service.get_status()` | ✅ метод добавлен |

**Acceptance:**
- [x] `python -c "from dsign.services.playback_service import PlaybackService"` на чистом venv
- [x] `GET /api/playback/status` без exception

---

## Фаза C — Content Resilience (Offline + Formats)

**Цель:** Плеер выживает без интернета. Больше форматов.

| ID | Задача | Статус | Зависимости |
|----|--------|--------|-------------|
| C1 | ContentCache (`content_cache.py`) | ⬜ | A3, network path |
| C2 | Audio-only + logo (imv) | ⬜ | — |
| C3 | Nested playlists (DB) | ⬜ | миграция модели |

### C1. ContentCache

**Acceptance:**
- [ ] [net1, net2, net3] — net2 предзагружен до EOF net1
- [ ] Offline — играет из кэша, не чёрный экран

### C2. Audio-Only + Logo

**Acceptance:**
- [ ] [video.mp4, audio.mp3, video2.mp4] — audio с logo

### C3. Nested Playlists

**Acceptance:**
- [ ] Nested playlist раскрывается в flat list при play

---

## Фаза B — API для управления (Remote Control)

**Цель:** Облако/скрипт полностью управляет плеером.

| ID | Статус | Что осталось |
|----|--------|--------------|
| B1 | 🟡 | `time_pos`, `duration`, `item_index`, `item_count`, `media_key`, `mpv_responsive`, `is_network`, `cache_state` |
| B2 | 🟡 | Вынести `health_check()` в `GET /api/health` + system/display/network aggregates |
| B3 | ⬜ | Emergency override + return_to_previous |
| B4 | ⬜ | `DSIGN_API_TOKEN` Bearer |
| B5 | ⬜ | **REST** seek/pause/skip (рекомендация: не чинить WS stub) |

### B1 — целевой ответ `/api/playback/status`

```json
{
  "status": "playing",
  "playlist_id": 42,
  "item_index": 3,
  "item_count": 10,
  "media_key": "promo_v2.mp4",
  "time_pos": 12.5,
  "duration": 30.0,
  "is_network": false,
  "mpv_responsive": true,
  "network_health": { "consecutive_ytdl_failures": 0, "cdn_may_be_down": false },
  "cache_state": { "cached_items": 2, "cache_size_mb": 150 }
}
```

---

## Фаза D — Ops (Эксплуатация)

**Цель:** Fleet без ручного вмешательства.

### D1. dsign-update (Self-Update)

**Статус:** ⬜ · **Требует D0**

- [ ] `check` / `download` / `apply` / `rollback`
- [ ] systemd timer (например 03:00)
- [ ] apply вызывает `dsign-apply-install`, не только `git pull`

**Acceptance:**
- [ ] Update downtime < 5 мин; rollback < 2 мин; fail не ломает систему

### D2. Local Schedule (SQLite)

**Статус:** ⬜ · после B3 (приоритеты override vs schedule)

**Acceptance:**
- [ ] Правило 09:00 → playlist A (±30 s)
- [ ] Offline 24 ч — расписание работает

---

## Итоговая таблица фаз

| Фаза | Задачи | Результат |
|------|--------|-----------|
| **D0** | manifest, verify, apply | Понятно, что на плеере и что залить |
| **A0** | network resilience | ✅ Rutube/VK hung, fast-fail (main) |
| **A** | A1–A5 | Zero-gap локали, safe loadfile |
| **C** | C1–C3 | Offline, audio-only, nested |
| **B** | B1–B5 | Remote control + monitoring |
| **D** | D1–D2 | OTA + автономное расписание |

---

## Коммерция (кратко)

4 фазы = **технический foundation**, не продукт продаж.

| Даёт foundation | Не заменяет |
|-----------------|-------------|
| Zero-gap, safe loadfile, health API | Fleet dashboard |
| OTA, schedule | Cloud SaaS, billing |
| Override API | Analytics, SLA |

**Стратегия:** A + D0 + B1/B2 → пилот интеграторам → Fleet Dashboard поверх foundation.

**Риск:** Dashboard до фикса playback = «красивый UI для глючного плеера».

---

## Журнал изменений этого документа

| Дата | Изменение |
|------|-----------|
| 2026-06-17 | Добавлены: сводка прогресса, D0, A0 (PR #80), порядок реализации, деплой/drift, чекбоксы acceptance |
