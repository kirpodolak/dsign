# DSign — сводный backlog (что осталось)

**Версия:** 2026-07-08 (commercial gap analysis)  
**Назначение:** единая точка входа — **только открытые задачи**. Закрыли пункт → `[x]` здесь и в исходном документе (колонка «Источник»).

**Уровни зрелости:** P0 (текущий backlog) = sellable edge player · **COM v1.0** = commercial launch · COM v1.5 = fleet scale · COM v2.0 = enterprise (отложено).

**Не дублирует:** выполненные фазы, спеки API, детальные prompt’ы — см. ссылки ниже.

---

## Исходные документы

| Документ | Роль | Когда открывать |
|----------|------|-----------------|
| [dsign_4phase_checklist.md](./dsign_4phase_checklist.md) | Фазы A→C→B→D, продуктовый roadmap | Контекст фазы, acceptance, Cursor prompt |
| [dsign_schedule_spec.md](./dsign_schedule_spec.md) | Спека расписания D2 (v1.5) | API, модель, UI расписания |
| [dsign_test_matrix.md](./dsign_test_matrix.md) | Ручные playback-тесты на плеере | Полевые проверки zero-gap, boot, network |
| [dsign_improvement_checklist.md](./dsign_improvement_checklist.md) | Production hardening (тесты, SRE) | Детали pytest, rate limit, subprocess |
| **Этот файл** | **Сводный backlog** | **С чего начать сегодня** |

---

## Уже сделано (не повторять)

Краткая сводка — детали в [4phase](./dsign_4phase_checklist.md).

| Область | Статус |
|---------|--------|
| D0 manifest + verify/apply | ✅ PR #82 |
| A0 network resilience | ✅ PR #80 |
| A1–A4 playback (код) | ✅ PR #85–#87 |
| A5 tech debt | ✅ |
| C1 ContentCache, C2 audio+logo | ✅ |
| B1–B5 API + Bearer playback | ✅ |
| **D2 расписание D2.1–D2.5** | ✅ PR #103–#107 |
| `GET /api/health`, upload limit 1 GiB, login rate limit | ✅ в коде |
| `MPVManager.shutdown()`, SIGTERM → `ScheduleEngine.stop()` | ✅ частично |
| **T-CI** GitHub Actions pytest (unit) | ✅ частично — integration/API smoke позже |
| **T-IPC** unit tests (`MpvJsonIpcSession`, 10 кейсов) | ✅ PR pytest-tier1 |
| **T-MPV** unit tests (`MPVManager._send_command`, 4 кейса) | ✅ PR #110 |
| **T-REC** recovery flows (9 кейсов) | ✅ PR t-rec-eof |
| **T-EOF** EOF detection 6 paths (7 кейсов) | ✅ PR t-rec-eof |
| Device snapshot telemetry (`GET /api/health`, `/playback/status`) | ✅ нет истории / fleet hub |
| Auth: local user + `is_admin` + Bearer token | ✅ не SSO / не multi-tenant |

> **Расхождения в 4phase:** сводка помечает D0/A1 ✅, но в теле документа остались старые `[ ]` — ориентируйтесь на сводку + код. Секцию D0/A1 в 4phase стоит синхронизировать отдельным PR.

---

## Рекомендуемый порядок работ

```mermaid
flowchart TD
  D1[D1 OTA] --> T1[pytest Tier 1 + CI]
  T1 --> H1[Hardening: rate limit, subprocess, Wi-Fi]
  H1 --> COM[COM v1.0: HTTPS, PoP, sec audit]
  COM --> ACC[Acceptance: test_matrix + D2 offline 24h]
  ACC --> C3[C3 nested playlists]
  C3 --> V15[COM v1.5: fleet config push]
  V15 --> P2[Polish: operator UI, alerts]
```

| Шаг | Фокус | Зачем сейчас |
|-----|-------|--------------|
| 1 | **D1 OTA** | Fleet без ручного `git pull`; зависит от D0 (уже есть) |
| 2 | **pytest Tier 1 + GitHub Actions** | Gate на regression до массового рефакторинга |
| 3 | **API hardening** | rate limit, subprocess timeouts, Wi-Fi validation |
| 4 | **Acceptance** | test_matrix + offline 24 ч расписания |
| 5 | **C3** | nested playlists — единственная открытая фаза C |
| 6 | **Polish** | graceful shutdown playback, cache retry, operator UI |

---

## Сводная таблица открытых задач

| ID | Задача | 🔴🟡🟢 | Источник | Зависимости |
|----|--------|--------|----------|-------------|
| **D1** | `dsign-update` OTA (check/download/apply/rollback + timer) | 🔴 | 4phase §D1 | D0 |
| **T-CI** | GitHub Actions: `pytest` на PR | 🟡 | improvement §1 | — |
| **T-IPC** | Unit: `MpvJsonIpcSession` | ✅ | improvement §1.1 | — |
| **T-MPV** | Unit: `MPVManager._send_command()` | ✅ | improvement §1.2 | T-IPC |
| **T-REC** | Integration: recovery flows | ✅ | improvement §1.3 | T-IPC, T-MPV |
| **T-EOF** | Integration: EOF detection (6 путей) | ✅ | improvement §1.4 | T-IPC, T-MPV |
| **T-API** | API smoke (auth, Bearer, schedule, CSRF→**400**) | 🔴 | improvement §1.5 | — |
| **T-SCH** | Unit/integration: `schedule_service`, exceptions, monthly | 🔴 | schedule §10, 4phase D2 | — |
| **T-AUD** | Integration: audio subsystem | 🔴 | improvement §1.6 | T-IPC |
| **H-RL** | Rate limiting API (play/stop/screenshot/reboot) | 🔴 | improvement §2 | — |
| **H-SUB** | Subprocess timeout audit (`amixer` и др.) | 🔴 | improvement §3 | — |
| **H-WIFI** | SSID/password validation (1–32, WPA 8–63) | 🔴 | improvement §5 | — |
| **H-UPL** | Upload: disk check до save, streaming >100MB | 🟡 | improvement §4 | частично ✅ 1 GiB |
| **D2-OPS** | `DSIGN_API_TOKEN` на fleet + проверка schedule Bearer | 🟡 | 4phase D2, schedule §D2.5 | — |
| **D2-24H** | Offline 24 ч — расписание по timezone | 🟡 | schedule §D2.4, 4phase D2 | — |
| **C3** | Nested playlists (DB + flat play) | 🟡 | 4phase §C3 | — |
| **H-SD** | Graceful shutdown playback (join thread, logo, DB) | 🟡 | improvement §6 | частично ✅ |
| **H-MEM** | `_media_backoff` TTL cleanup | 🟡 | improvement §7 | — |
| **H-PREF** | ContentCache: thread pool + cancel on playlist change | 🟡 | improvement §8 | — |
| **H-CACHE** | ContentCache download retry (exp backoff) | 🟡 | improvement §9 | — |
| **H-REF** | Refactor длинных методов (после тестов) | 🟡 | improvement §10 | T-* |
| **H-RQ** | Recovery queue вместо `blocking=False` skip | 🟡 | improvement §11 | — |
| **H-COAL** | Adaptive `DSIGN_MPV_RESTART_COALESCE_SEC` | 🟡 | improvement §12 | — |
| **P-DOC** | `docs/ENVIRONMENT.md` (env vars) | 🟢 | improvement §13 | — |
| **P-TYP** | mypy strict на critical paths | 🟢 | improvement §14 | — |
| **P-CFG** | Расширить `Config`, убрать дубли `os.getenv` | 🟢 | improvement §15 | частично ✅ |
| **P-UI** | Operator dashboard (HTML над `/api/health`) | 🟢 | improvement §17 | частично ✅ health API |
| **P-ALERT** | Webhook/email alerting | 🟢 | improvement §18 | P-UI |
| **COM-POP** | Proof of play (журнал показов + export) | 🔴 | commercial v1.0 | D2 ✅ |
| **COM-HTTPS** | HTTPS + secure cookies (install/docs) | 🔴 | commercial v1.0 | — |
| **COM-SEC** | Security audit: inputs, uploads, API | 🔴 | commercial v1.0 | H-WIFI |
| **COM-TEL** | Telemetry: история + опциональный push | 🟡 | commercial v1.0 | частично ✅ health |
| **COM-FLEET** | Remote config push (fleet-wide settings) | 🟡 | commercial v1.5 | D1, COM-TEL |
| **COM-RBAC** | Multi-tenant / роли beyond `is_admin` | ⏸ v2.0 | commercial v2.0 | fleet platform |
| **COM-SSO** | OAuth2 / SAML | ⏸ v2.0 | commercial v2.0 | COM-RBAC |

**⏸ v2.0** — в backlog для трассировки, **не планировать до fleet dashboard / central SaaS**.

---

## COM — Commercial launch (gap analysis 2026-07-08)

Задачи **для клиента / интегратора**, не playback engine. **dsign** = edge player (один объект = один инстанс), не multi-tenant SaaS.

| Тема | Что это | У нас сейчас | Делать? | Когда |
|------|---------|--------------|---------|-------|
| Proof of play | ROI / billing | Нет журнала показов, только `PlaybackStatus` | **Да** | COM v1.0 |
| Device telemetry | Fleet health | `GET /api/health` ✅ snapshot; нет истории/hub | **Частично** | v1.0 + v1.5 |
| HTTPS | Security baseline | nginx HTTP :80 в install | **Да** | COM v1.0 |
| Input sanitization | Security | CSRF, `secure_filename`; Wi‑Fi слабый | **Да** (audit) | COM v1.0 |
| Multi-tenant / RBAC | Enterprise | `is_admin` only | **Нет** | v2.0 |
| Remote config push | Fleet ops | `settings.json` локально | **Да** | v1.5 |
| SSO | Enterprise login | Local auth | **Нет** | v2.0 |

### COM-POP — Proof of play

- [ ] Таблица `playback_events`: `started_at`, `ended_at`, `playlist_id`, `item_key`, `source`, `rule_id`
- [ ] Запись при play/stop/смене item (PlaylistManager + ScheduleEngine)
- [ ] `GET /api/reports/playbacks?from=&to=` (JSON/CSV), Bearer
- [ ] Retention (например 90 дней)

### COM-HTTPS — TLS

- [ ] `docs/DEPLOY_HTTPS.md` (Let's Encrypt / reverse proxy интегратора)
- [ ] Опционально `install_dsign.sh --https`
- [ ] `SESSION_COOKIE_SECURE=true` при TLS

### COM-SEC — Security audit

- [ ] Audit всех inputs: POST body, upload, Wi‑Fi, paths, admin endpoints
- [ ] Закрыть вместе с **H-WIFI**, **H-RL**

### COM-TEL — Telemetry

- [ ] История health snapshots (SQLite или log rotate)
- [ ] `GET /api/telemetry/history` — локально
- [ ] Webhook периодический → см. **P-ALERT**

### COM-FLEET — Remote config (v1.5)

- [ ] API приёма fleet-wide settings (push или pull + etag)
- [ ] Audit log применений
- [ ] После **D1 OTA**

### ⏸ COM v2.0 — отложено

- **COM-RBAC**, **COM-SSO** — нужны **central fleet portal**, не плеер на Pi

---

## P0 — Продукт и блокеры production

### D1 — `dsign-update` OTA

- [ ] `check` / `download` / `apply` / `rollback`
- [ ] systemd timer (например 03:00)
- [ ] `apply` вызывает `dsign-apply-install`, не только `git pull`
- [ ] Acceptance: downtime < 5 мин; rollback < 2 мин; fail не ломает систему

*Источник:* [4phase §D1](./dsign_4phase_checklist.md)

### T-CI — Continuous Integration

- [x] Каталог `tests/` + `pytest` / `pytest-cov` (зависимости в `setup.py`, `packages=dsign` only)
- [x] GitHub Actions workflow на PR: **unit** (`tests/test_mpv_ipc_session.py`)
- [ ] Integration (fake MPV) + API smoke в том же workflow
- [ ] Merge gate: полный Tier 1 must pass

*Источник:* improvement §1, стратегия тестов

### T-IPC — `MpvJsonIpcSession` unit tests ✅

- [x] `command` / `commands_batch` (в т.ч. empty batch)
- [x] events: `subscribe_event`, `wait_event`, `drain_events`
- [x] `reset` / reconnect после reset
- [x] timeout, malformed JSON, concurrent commands, EOF

*Файлы:* `tests/test_mpv_ipc_session.py`, `tests/fake_mpv_ipc.py`  
*Фикс в проде:* reader игнорирует ошибки устаревшего сокета после `reset()`.

### T-REC — recovery flows ✅

- [x] `recover_after_mpv_systemd_restart` (resume index, advance, lock)
- [x] `get_resume_start_index_for_hung_recovery`
- [x] `consume_stall_recovery_advance`
- [x] hung recovery → post-restart callback
- [x] slideshow thread crash resume

*Файл:* `dsign/tests/test_playback_recovery.py`

### T-EOF — EOF detection ✅

- [x] end-file event (`mpv_end_file_eof`)
- [x] network idle (2× idle after stream_ready)
- [x] local idle
- [x] local time-pos stagnation
- [x] network near-EOF stagnation (VK/Rutube)
- [x] network duration reached (HLS)

*Файл:* `dsign/tests/test_playlist_eof_detection.py`  
*Фикс в проде:* не сбрасывать `consecutive_idle`, когда `idle-active` не опрашивался (чётные poll-тики сети).

### T-IPC … T-AUD — pytest Tier 1 (остальное)

Детальный список кейсов — в [dsign_improvement_checklist.md](./dsign_improvement_checklist.md) §1.1–1.6.

**Добавить к improvement (нет в оригинале):**

- [ ] `schedule_service`: `_monthly_matches`, exceptions, `expand_week` / `expand_month`
- [ ] API: `POST/DELETE /api/schedule/exceptions`, `GET /api/schedule/month`
- [ ] Bearer: schedule endpoints с `DSIGN_API_TOKEN` (без CSRF)

**Поправки ожиданий:**

- CSRF на `/api/*` → **400**, не 403 (`api_token_auth.py`)
- Тест rate limit play/stop → после **H-RL**, не до

### H-RL — Rate limiting API

- [ ] Flask-Limiter (или аналог) на mutating endpoints
- [ ] `POST /api/playback/play` — 5/min; `stop` — 10/min
- [ ] `POST /api/media/mpv_screenshot/capture` — 6/min
- [ ] `POST /api/system/services/*/restart` — 3/min (admin)
- [ ] `POST /api/system/reboot` — 1/hour (admin)
- [ ] Глобально: 100 req/min per IP

*Сейчас:* rate limit только на **login** (`auth_routes.py`).

*Источник:* improvement §2

### H-SUB — Subprocess timeouts

- [ ] `_audio_set()` → `amixer` с `timeout=3`
- [ ] Audit остальных `subprocess.run` без timeout (`settings_service`, `api_routes` display apply, …)
- [ ] `_run_nmcli` уже 20–45s — ок

*Источник:* improvement §3

### H-WIFI — Wi-Fi validation

- [ ] SSID: 1–32 символа, без control chars
- [ ] WPA password: 8–63 (если задан)
- [ ] Reject empty (уже есть) — расширить валидацию

*Сейчас:* только `strip()` + not empty (`connect_wifi_network`).

*Источник:* improvement §5

---

## P1 — Acceptance и функциональные хвосты

### D2 — расписание (ops + тест)

- [ ] **D2-24H:** отключить NTP 24 ч, слоты срабатывают по `settings.timezone`
- [ ] **D2-OPS:** на fleet задать `DSIGN_API_TOKEN`; curl Bearer на `/api/schedule/rules`

*Источник:* [schedule §D2.4–D2.5](./dsign_schedule_spec.md), [4phase §D2](./dsign_4phase_checklist.md)

### C3 — Nested playlists

- [ ] Модель / миграция nested playlist
- [ ] При play — раскрытие в flat list

*Источник:* [4phase §C3](./dsign_4phase_checklist.md)

### Ручные acceptance (плеер)

Не автоматизировать вместо pytest — прогон по [dsign_test_matrix.md](./dsign_test_matrix.md). Открытые из 4phase:

- [ ] **A3:** плейлист [valid, corrupt, valid] — skip corrupt
- [ ] **A4:** Rutube/HLS скорость; judder 29.97@60Hz
- [ ] **A1/A2:** 10/10 zero gap local video (VID-VID-001 в test_matrix)
- [ ] **C1:** prefetch net2 до EOF net1

---

## P2 — Hardening и polish (после P0)

### H-UPL — Upload (дополнить существующее)

**Уже есть:** `MAX_CONTENT_LENGTH` 1 GiB, post-save size check.

- [ ] Disk space check **до** сохранения файла
- [ ] Streaming upload для больших файлов (опционально снизить лимит на Pi)

*Источник:* improvement §4

### H-SD — Graceful shutdown

**Уже есть:** `MPVManager.shutdown()`, `MpvJsonIpcSession.close()`, SIGTERM → schedule engine stop.

- [ ] SIGTERM/SIGINT: `_stop_event` → join playback thread
- [ ] Idle logo перед exit; DB/session cleanup

*Источник:* improvement §6

### H-MEM, H-PREF, H-CACHE, H-RQ, H-COAL

См. [improvement §7–12](./dsign_improvement_checklist.md) — без дублирования текста.

### P-DOC … P-ALERT — Nice to have

- [ ] `docs/ENVIRONMENT.md`
- [ ] mypy `--strict` в CI
- [ ] Централизовать env в `Config` (расширить `dsign/config/config.py`)
- [ ] Operator UI поверх `GET /api/health` + `/api/playback/status`
- [ ] Alerting webhooks

---

## Быстрый старт для агента / разработчика

```
1. Открыть этот файл (dsign_backlog.md)
2. Выбрать ID из таблицы (например D1 или T-IPC)
3. Детали → исходный документ по колонке «Источник»
4. Закрыли → [x] здесь + в источнике + PR в колонке коммита
```

**Следующий логичный PR по продукту:** **D1 OTA**  
**Для commercial v1.0 после P0:** **COM-POP** + **COM-HTTPS** + **COM-SEC**  
**Следующий PR по качеству:** **T-API** или **T-SCH**

---

## Журнал backlog

| Дата | Изменение |
|------|-----------|
| 2026-07-09 | T-REC + T-EOF ✅ (16 pytest cases); network idle counter fix |
| 2026-07-08 | T-IPC ✅ (10 unit tests), T-CI частично (pytest workflow) |
| 2026-07-08 | COM v1.0/v1.5/v2.0: proof of play, HTTPS, sec audit, telemetry, fleet push |
| 2026-07-08 | Создан сводный backlog из 4phase + schedule_spec + improvement_checklist |
