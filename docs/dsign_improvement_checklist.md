# DSign — checklist улучшений (production hardening)

**Версия:** 2026-07-08  
**Статус:** открытые пункты сведены в **[dsign_backlog.md](./dsign_backlog.md)** — править задачи там.

Этот документ хранит **детали и обоснование** hardening-работ (тесты, SRE). Продуктовые фазы — в [dsign_4phase_checklist.md](./dsign_4phase_checklist.md).

---

## Как читать

| Поле | Смысл |
|------|--------|
| ID в backlog | `T-IPC`, `H-RL`, … — единый идентификатор в сводном чеклисте |
| 🔴 / 🟡 / 🟢 | Must / Should / Nice |
| Уже в коде | Не ставить задачу заново — см. колонку в [backlog](./dsign_backlog.md) |

---

## 🔴 Must have (открыто)

Полный список с зависимостями: [backlog §P0](./dsign_backlog.md#p0--продукт-и-блокеры-production).

### 1. Автоматизированные тесты → backlog `T-CI` … `T-AUD`, `T-SCH`

| Подраздел | Backlog ID | Фокус |
|-----------|------------|--------|
| 1.1 `MpvJsonIpcSession` | T-IPC ✅ | batch, events, reconnect, locks, timeout, malformed JSON — `tests/test_mpv_ipc_session.py` |
| 1.2 `MPVManager._send_command()` | T-MPV ✅ | retry, transport error, coalesced restart, vo switch — `tests/test_mpv_manager_send_command.py` |
| 1.3 Recovery flows | T-REC | `recover_after_mpv_systemd_restart`, boot resume, crash callback |
| 1.4 EOF detection | T-EOF | 6 путей end-file / idle / stagnation / HLS / VK |
| 1.5 API smoke | T-API | auth, Bearer, schedule; CSRF → **400**; rate limit после H-RL |
| 1.6 Audio | T-AUD | ALSA open, route rebind, mute |
| Schedule (добавлено) | T-SCH | monthly, exceptions, expand week/month |

**Зачем:** IPC + recovery + EOF — ядро uptime; без pytest нельзя безопасно рефакторить `playlist_management.py`.

### 2. Rate limiting API → `H-RL`

Сейчас limit только на login. Нужен на play/stop/screenshot/reboot — см. backlog.

### 3. Subprocess timeouts → `H-SUB`

Приоритет: `_audio_set()` / `amixer` без timeout.

### 4. Upload → `H-UPL` (🟡)

**Не делать:** базовый `MAX_CONTENT_LENGTH` (уже 1 GiB). Нужны: disk check до save, опционально streaming.

### 5. Wi-Fi SSID/password → `H-WIFI`

Расширить валидацию в `connect_wifi_network`.

---

## 🟡 Should have (открыто)

Сводка: [backlog §P2](./dsign_backlog.md#p2--hardening-и-polish-после-p0).

| § | Backlog | Примечание |
|---|---------|------------|
| 6 Graceful shutdown | H-SD | Schedule SIGTERM ✅; нужен join playback thread |
| 7 Memory leaks | H-MEM | `_media_backoff` без TTL |
| 8 Prefetch pool | H-PREF | сейчас thread per URL |
| 9 Cache retry | H-CACHE | нет exp backoff в `_download` |
| 10 Refactor long methods | H-REF | **только после** T-* |
| 11 Recovery queue | H-RQ | `blocking=False` skip |
| 12 Adaptive coalesce | H-COAL | сейчас фикс. 8s |

---

## 🟢 Nice to have (открыто)

| § | Backlog |
|---|---------|
| 13 ENV docs | P-DOC |
| 14 mypy strict | P-TYP |
| 15 Central config | P-CFG (расширить существующий `Config`) |
| 17 Metrics dashboard | P-UI (база: `GET /api/health` ✅) |
| 18 Alerting | P-ALERT |

---

## Стратегия тестов (архитектура)

### Tier 1 — must pass в CI

1. `MpvJsonIpcSession` ✅ (10 unit tests, fake AF_UNIX peer)
2. `MPVManager._send_command()` ✅ (unit tests + restart coalesce checks)
3. `PlaybackService` recovery
4. EOF detection (все пути)
5. API validation + auth
6. `schedule_service` (добавлено к оригинальному чеклисту)

### Tier 2 — should pass

7. Audio subsystem  
8. ContentCache LRU / prefetch / ffprobe  
9. Network reliability (ytdl, hung recovery)  
10. Mixed playlists  

### Tier 3 — nice

11. System API (Wi-Fi, screenshot, restart)  
12. Settings API  
13. Media upload / thumbnails  

### CI pipeline (целевой)

```
Unit (mock IPC) → Integration (fake MPV) → API smoke → coverage report → merge gate
```

Ручные сценарии zero-gap — [dsign_test_matrix.md](./dsign_test_matrix.md), не заменяют pytest.

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-07-08 | T-IPC: unit tests + reader stale-socket fix |
| 2026-07-08 | T-MPV: unit tests `_send_command` (vo guard, retries, coalesced restart) |
| 2026-07-08 | Перенесён в `docs/`; открытые задачи → `dsign_backlog.md` |
