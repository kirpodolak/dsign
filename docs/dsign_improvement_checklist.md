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
| 1.3 Recovery flows | T-REC ✅ | recover, hung restart, stall advance, slideshow crash — `tests/test_playback_recovery.py` |
| 1.4 EOF detection | T-EOF ✅ | 6 путей end-file / idle / stagnation / HLS / VK — `tests/test_playlist_eof_detection.py` |
| 1.5 API smoke | T-API ✅ | auth, Bearer, schedule; CSRF → **400** — `tests/test_api_smoke.py` |
| 1.6 Audio | T-AUD ✅ | ALSA open, route rebind, mute — `tests/test_audio_subsystem.py` |
| Schedule (добавлено) | T-SCH ✅ | monthly, exceptions, expand week/month — `tests/test_schedule_service.py` |

**Зачем:** IPC + recovery + EOF — ядро uptime; без pytest нельзя безопасно рефакторить `playlist_management.py`.

### 2. Rate limiting API → `H-RL` ✅

Login + mutating API endpoints: play/stop/screenshot/reboot/service restart — `api_rate_limit.py`, `test_api_rate_limit.py`.

### 3. Subprocess timeouts → `H-SUB`

Приоритет: `_audio_set()` / `amixer` без timeout.

### 4. Upload → `H-UPL` ✅

Disk check до save (`upload_disk.py`). Streaming chunked save для ≥100MB и unknown size (`upload_stream.py`, env `DSIGN_UPLOAD_STREAM_THRESHOLD_BYTES`, `DSIGN_UPLOAD_CHUNK_BYTES`). Tests: `test_upload_disk.py`, `test_upload_stream.py`.

### 5. Wi-Fi SSID/password → `H-WIFI` ✅

Расширить валидацию в `connect_wifi_network` — `wifi_validation.py`, `test_wifi_validation.py`.

### 6. Graceful shutdown → `H-SD` ✅

SIGTERM/SIGINT → `PlaybackService.graceful_shutdown()`: schedule stop, `playlist_manager.stop(join_timeout=…)`, idle logo, `mpv_manager.shutdown()`, `db.session.remove()`. Env: `DSIGN_SHUTDOWN_JOIN_SEC` (1–60s, default 8). Tests: `test_playback_graceful_shutdown.py`.

### 7. Memory leaks → `H-MEM` ✅

`_media_backoff` TTL prune — `media_backoff.py`, `DSIGN_MEDIA_BACKOFF_TTL_SEC`. Tests: `test_media_backoff_ttl.py`.

### 8. Prefetch pool → `H-PREF` ✅

ContentCache: `ThreadPoolExecutor` (`DSIGN_CONTENT_CACHE_PREFETCH_WORKERS`), `cancel_prefetches()` on playlist play/stop. Tests: `test_content_cache_prefetch.py`.

### 9. Cache retry → `H-CACHE` ✅

`_download` retries with exponential backoff (`content_cache_retry.py`, `DSIGN_CONTENT_CACHE_DOWNLOAD_ATTEMPTS`). Tests: `test_content_cache_retry.py`.

### 11. Recovery queue → `H-RQ` ✅

`RecoveryQueue` вместо silent skip при занятом `_recover_lock`. Env: `DSIGN_RECOVERY_QUEUE_MAX`. Tests: `test_recovery_queue.py`, `test_playback_recovery.py`.

---

## 🟡 Should have (открыто)

Сводка: [backlog §P2](./dsign_backlog.md#p2--hardening-и-polish-после-p0).

| § | Backlog | Примечание |
|---|---------|------------|
| 6 Graceful shutdown | H-SD | ✅ `graceful_shutdown`, join thread, idle logo, DB cleanup |
| 7 Memory leaks | H-MEM | ✅ TTL prune `_media_backoff` |
| 8 Prefetch pool | H-PREF | ✅ thread pool + cancel on playlist change |
| 9 Cache retry | H-CACHE | ✅ exp backoff в `_download` |
| 10 Refactor long methods | H-REF | ✅ PR1–4 (`playback_eof/network/slideshow/play`) |
| 11 Recovery queue | H-RQ | ✅ queue вместо `blocking=False` skip |
| 12 Adaptive coalesce | H-COAL | сейчас фикс. 8s |
| 13 ENV docs | P-DOC | ✅ `docs/ENVIRONMENT.md` |

---

## 🟢 Nice to have (открыто)

| § | Backlog |
|---|---------|
| 14 mypy strict | P-TYP |
| 15 Central config | P-CFG (расширить существующий `Config`) |
| 17 Metrics dashboard | P-UI (база: `GET /api/health` ✅) |
| 18 Alerting | P-ALERT |

---

## Стратегия тестов (архитектура)

### Tier 1 — must pass в CI

1. `MpvJsonIpcSession` ✅ (10 unit tests, fake AF_UNIX peer)
2. `MPVManager._send_command()` ✅ (unit tests + restart coalesce checks)
3. `PlaybackService` recovery ✅
4. EOF detection (все пути) ✅
5. API validation + auth ✅
6. `schedule_service` (добавлено) ✅

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
| 2026-07-10 | H-REF PR4: `playback_play.py` — play routing; H-REF series complete |
| 2026-07-08 | T-IPC: unit tests + reader stale-socket fix |
| 2026-07-09 | T-API + T-SCH: API smoke + schedule_service pytest |
| 2026-07-08 | Перенесён в `docs/`; открытые задачи → `dsign_backlog.md` |
