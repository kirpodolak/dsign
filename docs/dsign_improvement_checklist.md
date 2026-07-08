
# DSIGN — CHECKLIST УЛУЧШЕНИЙ (Improvement Checklist)
## Дата: 2026-07-08
## Цель: Привести проект к production-ready commercial state

================================================================================
КАК ЧИТАТЬ ЭТОТ ЧЕКЛИСТ
================================================================================

Каждый пункт содержит:
- [ ] Что делаем — конкретное действие
- ЗАЧЕМ — бизнес-цель (почему это важно для commercial viability)
- ДЛЯ КОГО — кто получает выгоду (разработчик, оператор, клиент, инвестор)
- КРИТИЧНОСТЬ — 🔴 Must have / 🟡 Should have / 🟢 Nice to have
- СЛОЖНОСТЬ — Low / Medium / High
- ЗАВИСИМОСТИ — что нужно сделать до этого пункта

================================================================================

🔴 КРИТИЧНЫЕ УЛУЧШЕНИЯ (Must Have — блокеры для production)
================================================================================

### 1. Автоматизированные тесты (pytest)

#### 1.1 Unit tests: MpvJsonIpcSession
- [ ] Тест batch commands (commands_batch)
- [ ] Тест event subscription (subscribe_event, wait_event, drain_events)
- [ ] Тест graceful reconnect (reset, _ensure_connected_and_reader)
- [ ] Тест thread-safety (_conn_lock + _pending_lock)
- [ ] Тест timeout handling
- [ ] Тест malformed JSON handling

ЗАЧЕМ: IPC session — ядро playback engine. Баг здесь = dead playback у клиента.
       Без тестов невозможно рефакторить без риска regression.
ДЛЯ КОГО: Разработчик (рефакторинг), CI/CD (gate на PR), клиент (стабильность).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

#### 1.2 Unit tests: MPVManager._send_command()
- [ ] Тест retry logic (3 attempts с adaptive delays)
- [ ] Тест transport error detection (_is_ipc_transport_error)
- [ ] Тест coalesced restart (_restart_systemd_service_if_needed)
- [ ] Тест vo switch blocking during playback
- [ ] Тест show-text truncation
- [ ] Тест slow command detection (>250ms)

ЗАЧЕМ: 300+ строк сложной логики. Баг в retry = infinite loop или missed restart.
ДЛЯ КОГО: Разработчик, SRE (debug production issues).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: 1.1

#### 1.3 Integration tests: PlaybackService recovery flows
- [ ] Тест recover_after_mpv_systemd_restart()
- [ ] Тест _resume_playback_after_boot()
- [ ] Тест _on_slideshow_thread_crash() + callback
- [ ] Тест consume_stall_recovery_advance()
- [ ] Тест hung recovery (_schedule_hung_recovery, _run_hung_recovery)

ЗАЧЕМ: Recovery — критический путь. Баг = dead playback после restart.
       Только integration test может проверить взаимодействие компонентов.
ДЛЯ КОГО: Разработчик, клиент (uptime), SRE (incident response).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: High
ЗАВИСИМОСТИ: 1.1, 1.2

#### 1.4 Integration tests: EOF detection
- [ ] Тест end-file event path
- [ ] Тест idle-active path
- [ ] Тест time-pos stagnation path
- [ ] Тест IPC dead during EOF wait
- [ ] Тест near-EOF early advance (HLS)
- [ ] Тест instant EOF (VK edl://)

ЗАЧЕМ: EOF detection — 400+ строк. 6 путей. Баг = hang или skip.
ДЛЯ КОГО: Клиент (gapless playback), разработчик (refactoring).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: High
ЗАВИСИМОСТИ: 1.1, 1.2

#### 1.5 API smoke tests
- [ ] Тест POST /api/playback/play (validation, auth)
- [ ] Тест POST /api/playback/stop
- [ ] Тест GET /api/playback/status
- [ ] Тест rate limiting (rapid play/stop → 429)
- [ ] Тест CSRF (screenshot без token → 403)
- [ ] Тест admin checks (restart без admin → 403)

ЗАЧЕМ: API — точка входа для всех операций. Баг = security vulnerability
       или broken client experience.
ДЛЯ КОГО: Разработчик, security auditor, клиент (API stability).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

#### 1.6 Integration tests: Audio subsystem
- [ ] Тест _ensure_mpv_alsa_pcm_open() (3 fallback methods)
- [ ] Тест _sync_settings_audio_route_to_mpv() (rebind без restart)
- [ ] Тест _maybe_clear_global_mute_on_item_start()
- [ ] Тест try_clear_global_mute_on_volume()
- [ ] Тест reapply_effective_mute_to_mpv()

ЗАЧЕМ: Audio — частая причина support tickets. "No audio" = unhappy client.
ДЛЯ КОГО: Клиент (audio reliability), support (fewer tickets).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: 1.1, 1.2

### 2. Rate limiting API

- [ ] Добавить Flask-Limiter
- [ ] POST /api/playback/play — max 5 req/min per user
- [ ] POST /api/playback/stop — max 10 req/min per user
- [ ] POST /api/media/mpv_screenshot/capture — max 6 req/min per user
- [ ] POST /api/system/services/<svc>/restart — max 3 req/min per admin
- [ ] POST /api/system/reboot — max 1 req/hour per admin
- [ ] Глобальный rate limit: 100 req/min per IP

ЗАЧЕМ: Защита от race conditions (rapid play/stop), DDoS systemd,
       accidental abuse. Без rate limiting один оператор может
       положить всю систему rapid clicks.
ДЛЯ КОГО: SRE (system stability), оператор (защита от accidental abuse),
           клиент (uptime).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

### 3. Subprocess timeouts

- [ ] _audio_set() — subprocess.run(timeout=3.0) для amixer
- [ ] _run_nmcli() — subprocess.run(timeout=10.0)
- [ ] _display_apply() — subprocess.run(timeout=15.0)
- [ ] Все остальные subprocess calls — audit + добавить timeouts

ЗАЧЕМ: Broken ALSA / hung nmcli = infinite hang = dead HTTP worker.
       Flask worker pool исчерпывается, API перестаёт отвечать.
ДЛЯ КОГО: SRE (system stability), клиент (API availability).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

### 4. Upload file size limits

- [ ] MAX_CONTENT_LENGTH = 500MB (configurable)
- [ ] Проверка Content-Length перед чтением
- [ ] Streaming upload для файлов >100MB
- [ ] Disk space check перед upload

ЗАЧЕМ: 4GB видео → OOM → kill Flask → dead API.
       Raspberry Pi имеет ограниченную RAM.
ДЛЯ КОГО: SRE (prevent OOM), клиент (reliable upload).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

### 5. SSID / password sanitization

- [ ] Validate SSID format (1-32 chars, no control chars)
- [ ] Escape special chars перед nmcli
- [ ] Validate password length (8-63 chars for WPA2)
- [ ] Reject empty SSID

ЗАЧЕМ: SSID с кавычками или control chars = broken nmcli command
       или потенциальный command injection.
ДЛЯ КОГО: Security auditor, клиент (Wi-Fi reliability).
КРИТИЧНОСТЬ: 🔴 Must have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

================================================================================

🟡 ВАЖНЫЕ УЛУЧШЕНИЯ (Should Have — значительно улучшают quality of life)
================================================================================

### 6. Graceful shutdown

- [ ] SIGTERM handler в PlaybackService
- [ ] _stop_event.set() → join playback thread
- [ ] MPVManager.shutdown() — quit + cleanup
- [ ] LogoManager.display_idle_logo() перед exit
- [ ] DB connection cleanup
- [ ] Socket cleanup

ЗАЧЕМ: systemctl stop digital-signage → clean exit без corrupted state.
       Сейчас playback thread killed mid-item → potential DB lock.
ДЛЯ КОГО: SRE (clean deploys), клиент (no playback interruption on update).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

### 7. Memory leak fixes

- [ ] _media_backoff — periodic cleanup entries >24h
- [ ] _loadfile_history — cleanup old entries
- [ ] _amixer_pick_cache — TTL уже есть (60s), проверить
- [ ] _system_status_cache — TTL 2s, ок

ЗАЧЕМ: 1000+ unique failing URLs → unbounded dict growth → OOM.
       Embedded device (Pi) имеет ограниченную RAM.
ДЛЯ КОГО: SRE (prevent OOM), клиент (long-term stability).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

### 8. Thread pool для prefetch

- [ ] ThreadPoolExecutor(max_workers=4) для ContentCache.prefetch_async()
- [ ] Queue для pending prefetch tasks
- [ ] Cancel pending при playlist change

ЗАЧЕМ: 100+ items playlist → 100+ concurrent threads → OOM или
       resource exhaustion. Thread pool = controlled concurrency.
ДЛЯ КОГО: SRE (resource management), клиент (stable performance).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

### 9. ContentCache retry

- [ ] _download() — 3 attempts с exponential backoff (2s, 4s, 8s)
- [ ] Retry только на transient errors (timeout, 5xx)
- [ ] No retry на permanent errors (404, 403)
- [ ] Log each attempt

ЗАЧЕМ: Transient network issues (CDN hiccup) → 1 attempt = fail.
       Retry = resilience. Но retry на 404 = waste of bandwidth.
ДЛЯ КОГО: Клиент (offline playback reliability), SRE (fewer manual interventions).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

### 10. Refactoring длинных методов

- [ ] _send_command() → extract _send_command_attempt(), _handle_transport_error()
- [ ] _manual_slideshow_loop() → extract _play_video_item(), _play_image_item(), _wait_item_end()
- [ ] _wait_mpv_video_end() → extract _wait_eof_event(), _wait_idle(), _detect_stagnation()

ЗАЧЕМ: 300-900 строк = невозможно тестировать отдельные пути.
       Extract → unit test each path → confident refactoring.
ДЛЯ КОГО: Разработчик (maintainability), CI/CD (faster test runs).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: 1.x (тесты должны быть ДО refactoring)

### 11. Recovery queue вместо skip

- [ ] _recover_lock blocking=False → queue recovery attempts
- [ ] Max queue size (3 attempts)
- [ ] Deduplicate identical recovery requests

ЗАЧЕМ: Rapid restart'ы (systemd flapping) → blocking=False skip =
       missed recovery. Queue = guaranteed processing.
ДЛЯ КОГО: SRE (reliability), клиент (recovery after flapping).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

### 12. Adaptive restart coalesce

- [ ] DSIGN_MPV_RESTART_COALESCE_SEC: 8s → 15s → 30s на consecutive failures
- [ ] Reset на success
- [ ] Log coalesce escalation

ЗАЧЕМ: Flapping CDN → fixed 8s = restart storm. Adaptive =
       backoff для systemd, fewer restart cycles.
ДЛЯ КОГО: SRE (system stability), клиент (less flickering).
КРИТИЧНОСТЬ: 🟡 Should have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

================================================================================

🟢 MEDIUM УЛУЧШЕНИЯ (Nice to Have — улучшают DX и документацию)
================================================================================

### 13. Документация env vars

- [ ] Создать docs/ENVIRONMENT.md
- [ ] Таблица: name | default | description | when to tune | example
- [ ] Группировка по категориям (playback, network, audio, cache, debug)
- [ ] Cross-reference с кодом (line numbers)

ЗАЧЕМ: 70+ env vars разбросаны по коду. Новый разработчик или SRE
       тратит часы на reverse engineering. Документация = onboarding.
ДЛЯ КОГО: Разработчик (onboarding), SRE (tuning), клиент (self-service).
КРИТИЧНОСТЬ: 🟢 Nice to have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

### 14. Type hints (mypy --strict)

- [ ] Добавить type hints в critical paths
- [ ] Запустить mypy --strict в CI
- [ ] Исправить все Any в MPVManager, PlaybackService

ЗАЧЕМ: Type safety = fewer runtime bugs. mypy ловит 30% багов
       до deploy. Но требует initial investment.
ДЛЯ КОГО: Разработчик (fewer bugs), CI/CD (static analysis).
КРИТИЧНОСТЬ: 🟢 Nice to have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

### 15. Central config class

- [ ] Создать Config class вместо scattered os.getenv()
- [ ] Validation при старте (invalid values → clear error)
- [ ] Typed access: config.mpv_ipc_timeout вместо os.getenv("DSIGN_MPV_IPC_TIMEOUT", 6.0)

ЗАЧЕМ: Scattered os.getenv() = трудно найти, легко опечататься.
       Central config = single source of truth + validation.
ДЛЯ КОГО: Разработчик (maintainability), SRE (clear errors).
КРИТИЧНОСТЬ: 🟢 Nice to have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: 13 (документация env vars)

### 16. LogoManager race fix

- [ ] Заменить exists() + access() на try/except
- [ ] Atomic file operations

ЗАЧЕМ: exists() не atomic. Между проверкой и использованием файл
       может измениться. try/except = race-safe.
ДЛЯ КОГО: Разработчик (correctness).
КРИТИЧНОСТЬ: 🟢 Nice to have
СЛОЖНОСТЬ: Low
ЗАВИСИМОСТИ: Нет

### 17. Metrics dashboard (операторская страница)

- [ ] /metrics или /dashboard endpoint
- [ ] Playback health: uptime, current item, buffer state
- [ ] Network health: online/offline, cache fill %, last refresh
- [ ] System health: CPU, temp, storage, display mode
- [ ] Audio health: current route, volume, mute state
- [ ] Alert history: last 10 alerts с timestamps
- [ ] Auto-refresh каждые 5 секунд

ЗАЧЕМ: Оператор видит состояние без SSH. Быстрая диагностика
       проблем. Может показать клиенту "всё работает".
ДЛЯ КОГО: Оператор (visibility), клиент (transparency), SRE (MTTR).
КРИТИЧНОСТЬ: 🟢 Nice to have
СЛОЖНОСТЬ: Medium
ЗАВИСИМОСТИ: Нет

### 18. Alerting (уведомления оператору)

- [ ] Webhook на проблемы (playback stuck, network down, high temp)
- [ ] Email/SMS на critical (system crash, disk full)
- [ ] Alert suppression (не спамить на known issues)
- [ ] Alert history в DB

ЗАЧЕМ: Proactive alerting вместо reactive "клиент позвонил".
       MTTR сокращается с часов до минут.
ДЛЯ КОГО: SRE (proactive ops), клиент (less downtime).
КРИТИЧНОСТЬ: 🟢 Nice to have
СЛОЖНОСТЬ: High
ЗАВИСИМОСТИ: 17 (metrics dashboard)

================================================================================

ТЕСТЫ: ПОЛНАЯ СТРАТЕГИЯ
================================================================================

## Для кого тесты

| Аудитория | Что получает | Как использует |
|-----------|-------------|----------------|
| **Разработчик** | Уверенность при refactoring | Запускает pytest локально перед commit |
| **CI/CD (GitHub Actions)** | Gate на merge | Блокирует PR если тесты падают |
| **SRE / Оператор** | Health metrics | Смотрит dashboard, получает alerts |
| **Клиент** | Стабильный продукт | Не замечает regression'ов |
| **Инвестор** | Доказательство зрелости | Смотрит coverage badge |

## Что тестируем (приоритеты)

### Tier 1: Critical Path (must pass всегда)
1. MpvJsonIpcSession — IPC protocol correctness
2. MPVManager._send_command() — retry, recovery, restart
3. PlaybackService recovery — systemd restart, boot resume, crash recovery
4. EOF detection — все 6 путей
5. API validation — auth, input validation, rate limiting

### Tier 2: Important (should pass)
6. Audio subsystem — ALSA open, route rebind, mute logic
7. ContentCache — LRU, prefetch, ffprobe
8. Network reliability — ytdl, proactive refresh, mid-stream reload
9. Mixed playlists — image timer, transitions, gapless

### Tier 3: Nice to have (can fail occasionally)
10. System API — Wi-Fi, screenshot, service restart
11. Settings API — display mode, transcode, preview timer
12. Media API — upload, folders, thumbnails

## Как тесты работают (архитектура)

```
┌─────────────────────────────────────────────────────────────┐
│                    CI/CD Pipeline (GitHub Actions)           │
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐  │
│  │ Unit Tests  │ → │ Integration │ → │  API Smoke      │  │
│  │ (pytest)    │    │ Tests       │    │  Tests          │  │
│  │             │    │             │    │                 │  │
│  │ • IPC mock  │    │ • Fake MPV  │    │ • Flask client  │  │
│  │ • Assert    │    │ • Assert    │    │ • Assert        │  │
│  │   protocol  │    │   recovery  │    │   status codes  │  │
│  └─────────────┘    └─────────────┘    └─────────────────┘  │
│         │                  │                   │             │
│         ▼                  ▼                   ▼             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Coverage Report (codecov)                │  │
│  │  • Line coverage %                                    │  │
│  │  • Branch coverage %                                  │  │
│  │  • Coverage diff (новый PR vs main)                   │  │
│  └──────────────────────────────────────────────────────┘  │
│                           │                                  │
│                           ▼                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Merge Gate                                │  │
│  │  • Все тесты pass → MERGE ALLOWED                     │  │
│  │  • Любой тест fail → MERGE BLOCKED                    │  │
│  │  • Coverage < 80% → MERGE BLOCKED (warning)         │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Куда выводим результат

### 1. Локально (разработчик)
```bash
$ pytest tests/ -v --cov=dsign --cov-report=term-missing

tests/test_mpv_ipc.py::test_batch_commands PASSED
tests/test_mpv_ipc.py::test_event_subscription PASSED
tests/test_recovery.py::test_systemd_restart_recovery PASSED
tests/test_recovery.py::test_boot_resume PASSED
...

Coverage: 87%
Missing lines:
  dsign/services/playback_service.py: 1245-1250, 1340-1355
```

### 2. CI/CD (GitHub Actions)
- PR comment с результатами тестов
- Coverage badge в README
- Merge blocked если тесты падают

### 3. Метрики / Dashboard (оператор)
```
┌─────────────────────────────────────────────────────────────┐
│  DSIGN Health Dashboard                    [Auto-refresh 5s] │
├─────────────────────────────────────────────────────────────┤
│  Playback        │  Network        │  System                │
│  ─────────────   │  ────────────   │  ────────              │
│  Status: 🟢 OK   │  Online: 🟢 Yes │  CPU: 12%              │
│  Item: Video #3  │  Cache: 45%     │  Temp: 52°C            │
│  Buffer: 85%     │  Last refresh:  │  Storage: 23GB free    │
│  Uptime: 14d     │    2 min ago    │  Display: 1080p60      │
│                  │                  │                        │
│  Audio           │  Alerts (last 24h)                       │
│  ─────           │  ────────────────                       │
│  Route: ALSA     │  🟡 14:32 Proactive refresh triggered   │
│  Volume: 80%     │  🟢 12:15 Recovery completed            │
│  Mute: OFF       │  🟡 08:45 ytdl timeout (recovered)      │
│                  │  🔴 03:12 Systemd restart (hung)        │
└─────────────────────────────────────────────────────────────┘
```

### 4. Alerts (SRE / Оператор)
```
🔴 CRITICAL: Playback stuck for 90s
   Device: dsign-001 (Client: ABC Mall)
   Last item: Rutube HLS #7
   Action: Auto-recovery initiated
   Time: 2026-07-08 14:32:15 UTC
```

## Практический смысл для бизнеса

| Метрика | Без тестов | С тестами |
|---------|-----------|-----------|
| MTTR (Mean Time To Recovery) | 2-4 часа | 15-30 минут |
| Regression frequency | Каждый 3-й deploy | < 1% deploys |
| Onboarding time нового dev | 2-3 недели | 3-5 дней |
| Client churn из-за багов | 15-20% | < 5% |
| Support ticket volume | Высокий | Низкий |
| Investor confidence | Средний | Высокий |

================================================================================

ПРИОРИТИЗАЦИЯ (Roadmap)
================================================================================

### Спринт 1 (1 неделя) — Foundation
- [ ] 1.1 Unit tests: MpvJsonIpcSession
- [ ] 1.5 API smoke tests
- [ ] 2. Rate limiting
- [ ] 3. Subprocess timeouts

### Спринт 2 (1 неделя) — Reliability
- [ ] 1.2 Unit tests: MPVManager._send_command()
- [ ] 1.3 Integration tests: recovery flows
- [ ] 1.4 Integration tests: EOF detection
- [ ] 6. Graceful shutdown

### Спринт 3 (1 неделя) — Polish
- [ ] 1.6 Integration tests: audio subsystem
- [ ] 7. Memory leak fixes
- [ ] 8. Thread pool для prefetch
- [ ] 9. ContentCache retry

### Спринт 4 (1 неделя) — Refactoring
- [ ] 10. Refactoring длинных методов
- [ ] 11. Recovery queue
- [ ] 12. Adaptive coalesce
- [ ] 13. Документация env vars

### Спринт 5 (1 неделя) — Operator Experience
- [ ] 17. Metrics dashboard
- [ ] 18. Alerting
- [ ] 14. Type hints
- [ ] 15. Central config class

================================================================================

ИТОГОВАЯ ОЦЕНКА ПОСЛЕ ВСЕХ УЛУЧШЕНИЙ
================================================================================

| Компонент | Текущее | После улучшений |
|-----------|---------|-----------------|
| Playback engine | 9.7/10 | 9.9/10 |
| Content support | 9.5/10 | 9.8/10 |
| API layer | 9.0/10 | 9.7/10 |
| System integration | 9.5/10 | 9.8/10 |
| Tests | 0/10 | 8.5/10 |
| Documentation | 5/10 | 8.5/10 |
| Operator experience | 6/10 | 9.0/10 |
| **ОБЩАЯ ОЦЕНКА** | **9.7/10** | **9.8/10** |

Основной gain — не в коде, а в **confidence** (уверенности).
С тестами можно деплоить в пятницу вечером и спать спокойно.
