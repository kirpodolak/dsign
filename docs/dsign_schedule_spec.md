# D2 Schedule Engine — Спецификация

**Версия:** 1.1 (review-amended)  
**Статус:** Готово к реализации D2.1–D2.4  
**Репозиторий:** `kirpodolak/dsign` (Flask + SQLite + MPV + Wayland/labwc)  
**Прототип UI:** `docs/Расписание протип.html` (demo 08:00–22:00; production — 00:00–23:59)

### Changelog 1.0 → 1.1

- Пути файлов приведены к реальному layout репозитория (`extensions.py`, `routes/api/`).
- Inline-миграция — по образцу `_ensure_playlist_sort_order_column`.
- `GET /week`: поля для day-transfer UI, disabled rules на сетке, timezone через `_local_now()`.
- `source` / `rule_id` — propagation через `PlaylistManager._play_impl`.
- D2.1: `evaluate_now()` — stub; полная wiring в D2.2.
- D2.3: CSRF, i18n, empty-click, context menu в чеклисте.
- Graceful shutdown, `schedule_conflict_resolved`, запрет слотов через полночь.

---

## 1. Архитектура (целевая)

```
┌─────────────────────────────────────────────────────────────┐
│  UI (index.html) — вкладки «Плейлисты» | «Расписание»     │
│  ├─ Сетка 00:00–23:59, drag/resize, прогресс-бар           │
│  ├─ Slide-panel: playlist, дни, время, repeat, priority    │
│  └─ Сайдбар: playback source badge + «Вернуться к сетке»  │
├─────────────────────────────────────────────────────────────┤
│  API (Flask)                                               │
│  ├─ /api/schedule/rules — CRUD                            │
│  ├─ /api/schedule/week — развёрнутые инстансы             │
│  ├─ /api/schedule/now — активное правило + следующее      │
│  └─ /api/playback/return-to-schedule                      │
├─────────────────────────────────────────────────────────────┤
│  ScheduleEngine (singleton, daemon timer, 30 с)            │
│  ├─ _evaluate() → find active rule → play/stop             │
│  ├─ evaluate_now() — boot, override return, post-mutate    │
│  └─ _local_now() — timezone-aware (settings.timezone)      │
├─────────────────────────────────────────────────────────────┤
│  PlaybackService                                           │
│  ├─ play(playlist_id, source, rule_id)                    │
│  ├─ stop(source)                                          │
│  ├─ play_override() → сохраняет previous_* в PlaybackStatus│
│  └─ boot resume: schedule-first, не last manual            │
├─────────────────────────────────────────────────────────────┤
│  MPV + Wayland (imv logo underneath)                       │
└─────────────────────────────────────────────────────────────┘
```

### 1.1. Layout репозитория (не invent)

| Компонент | Путь в dsign |
|-----------|--------------|
| Модели | `dsign/models.py` (+ опционально `dsign/models/schedule.py` с re-export) |
| Inline-миграция | `dsign/extensions.py` → `_ensure_schedule_schema(app)` |
| API | `dsign/routes/api/api_routes.py` или blueprint + `init_routes()` |
| Engine | `dsign/services/schedule_engine.py` |
| Time helper | `dsign/services/schedule_time.py` → `local_now(settings_service)` |
| UI | `dsign/templates/index.html`, `static/js/schedule.js`, `static/css/schedule.css` |

**Нет Alembic** — только `db.create_all()` + `ALTER TABLE` при старте (как `sort_order`).

---

## 2. Модель данных

### 2.1. ScheduleRule

```python
class ScheduleRule(db.Model):
    __tablename__ = 'schedule_rules'

    id          = Column(Integer, primary_key=True)
    playlist_id = Column(Integer, ForeignKey('playlists.id'), nullable=False)
    enabled     = Column(Boolean, default=True)
    archived_at = Column(DateTime, nullable=True)        # soft delete

    days_of_week = Column(Integer, default=0)             # bitmask: Пн=1 … Вс=64
    start_time   = Column(Time, nullable=False)
    end_time     = Column(Time, nullable=False)

    repeat_type  = Column(String(16), default='weekly')   # weekly | once
    valid_from   = Column(Date, nullable=True)            # обязателен для once
    valid_until  = Column(Date, nullable=True)

    priority     = Column(Integer, default=5)             # 1–10, меньше = сильнее

    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    playlist = relationship('Playlist', backref='schedule_rules')
```

### 2.2. Расширение PlaybackStatus

```python
class PlaybackStatus(db.Model):
    __tablename__ = 'playback_status'
    id           = Column(Integer, primary_key=True)      # always 1
    status       = Column(String(16))                     # playing | stopped | idle
    playlist_id  = Column(Integer, ForeignKey('playlists.id'))
    timestamp    = Column(Integer, ...)                   # existing

    source              = Column(String(16), default='idle')  # manual|schedule|override|idle
    rule_id             = Column(Integer, ForeignKey('schedule_rules.id'), nullable=True)
    previous_source     = Column(String(16), nullable=True)
    previous_rule_id    = Column(Integer, ForeignKey('schedule_rules.id'), nullable=True)
    previous_playlist_id = Column(Integer, ForeignKey('playlists.id'), nullable=True)
```

### 2.3. Inline-миграция

```python
# dsign/extensions.py — вызывать из init_extensions() после db.create_all()

def _ensure_schedule_schema(app) -> None:
    """SQLite: schedule_rules table + playback_status columns."""
    from sqlalchemy import inspect, text
    from dsign.models import ScheduleRule  # noqa

    try:
        insp = inspect(db.engine)
        tables = insp.get_table_names()
        if 'schedule_rules' not in tables:
            ScheduleRule.__table__.create(db.engine)
            app.logger.info('Created schedule_rules table')

        if 'playback_status' not in tables:
            return
        cols = {c['name'] for c in insp.get_columns('playback_status')}
        for col_name, col_type in [
            ('source', 'VARCHAR(16)'),
            ('rule_id', 'INTEGER'),
            ('previous_source', 'VARCHAR(16)'),
            ('previous_rule_id', 'INTEGER'),
            ('previous_playlist_id', 'INTEGER'),
        ]:
            if col_name not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE playback_status ADD COLUMN {col_name} {col_type}'
                    ))
                app.logger.info('Added playback_status.%s', col_name)
    except Exception as e:
        app.logger.warning('schedule schema migration skipped: %s', e)
        db.session.rollback()
```

---

## 3. Приоритеты источников (runtime stack)

```
┌──────┬─────────────────┬────────────────────────────────────────────┐
│ Level│ Source          │ Behavior                                   │
├──────┼─────────────────┼────────────────────────────────────────────┤
│  1   │ Override (B3)   │ До конца single-pass; return → previous_*  │
│  2   │ Manual (UI)     │ До Stop или «Вернуться к расписанию»       │
│  3   │ Schedule        │ Авто по правилам; тик 30 с                 │
│  4   │ Idle            │ Нет активного слота; idle logo             │
└──────┴─────────────────┴────────────────────────────────────────────┘
```

**Правила:**

- ScheduleEngine **не трогает** playback при `source in ('override', 'manual')`.
- Manual play блокирует расписание до Stop или `POST /api/playback/return-to-schedule`.
- `play_override()` выставляет `source='override'` и сохраняет `previous_*` в **PlaybackStatus** (и в `_override_return_ctx` для item_index).
- Override return по `previous_source`:
  - `schedule` → `evaluate_now()` → play или idle;
  - `manual` → play previous playlist + `source='manual'`;
  - иначе → `stop(source='schedule')` → idle.

**Propagation `source` (обязательно):**

| Entry point | source |
|-------------|--------|
| `POST /api/playback/play` | `manual` |
| `POST /api/playback/stop` | `manual` → status `stopped` |
| `play_override()` | `override` |
| `ScheduleEngine._evaluate` | `schedule` |
| Конец слота / нет правила | `stop(source='schedule')` → `idle` |

Запись в БД — в **`PlaylistManager._play_impl`** и **`_stop_impl`** (не только в обёртке `PlaybackService`).

---

## 4. Timezone

```python
# dsign/services/schedule_time.py

import zoneinfo
from datetime import datetime

DEFAULT_TZ = 'Europe/Moscow'

def local_now(settings_service=None) -> datetime:
    tz_name = DEFAULT_TZ
    if settings_service:
        try:
            tz_name = settings_service.load_settings().get('timezone', DEFAULT_TZ)
        except Exception:
            pass
    try:
        return datetime.now(zoneinfo.ZoneInfo(tz_name))
    except Exception:
        return datetime.now()
```

- Engine и **все** schedule API (`/week`, `/now`, `is_playing_now`) используют **один** helper.
- Default в `settings_service.DEFAULT_SETTINGS`: `"timezone": "Europe/Moscow"`.
- UI timezone — **D2.4**; engine работает с default до появления UI.

**MVP:** слоты **не пересекают полночь** (`start_time < end_time`, оба в 00:00–23:59).

---

## 5. ScheduleEngine

### 5.1. Lifecycle

- **Singleton** на `PlaybackService`: `self._schedule_engine`.
- `start()` — в `_init_background_loop` **после** `set_app` (нужен `app.app_context()`).
- `stop()` — `self._running = False` + `timer.cancel()`.
- Timer: **`daemon=True`** (как остальные фоновые потоки dsign).
- **Graceful shutdown (опционально D2.2):** `signal.SIGTERM` / `SIGINT` → `engine.stop()`. Для MVP достаточно `daemon=True`; отдельного shutdown hook в проекте сейчас нет.

### 5.2. `_evaluate()` (псевдокод)

```python
def _evaluate(self):
    with self._app.app_context():
        current_source = self._get_current_source()
        if current_source in ('override', 'manual'):
            return

        now = local_now(self.settings_service)
        active_rule = self._find_active_rule(now)

        if active_rule:
            current_rule_id = self._get_current_rule_id()
            current_playlist = self._get_current_playlist_id()
            # Не рестартить, если уже играет тот же playlist по тому же rule
            if current_rule_id != active_rule.id:
                if current_rule_id == active_rule.id and current_playlist == active_rule.playlist_id:
                    return
                self.playback.play(
                    active_rule.playlist_id,
                    source='schedule',
                    rule_id=active_rule.id,
                )
        else:
            if self._get_current_rule_id() is not None:
                self.playback.stop(source='schedule')
```

### 5.3. `_find_active_rule`

Фильтры: `enabled`, `archived_at IS NULL`, bitmask дня, `start_time <= now.time() < end_time` (end exclusive), weekly/once, `valid_until`.

Сортировка: `priority ASC`, `id ASC`. Победитель — `rules[0]`.

**Лог `schedule_conflict_resolved`:** если кандидатов > 1, INFO с `chosen_rule_id`, `skipped_rule_ids`, `now`.

### 5.4. `evaluate_now()` hooks

| Событие | Действие |
|---------|----------|
| D2.1 API mutate | stub / no-op (engine ещё нет) |
| D2.2+ POST/PUT/toggle/archive | `engine.evaluate_and_apply()` |
| Boot resume | `evaluate_now()` |
| Override return | `evaluate_now()` если `previous_source == 'schedule'` |
| `return-to-schedule` | `evaluate_now()` + play/stop |

---

## 6. PlaybackService / PlaylistManager

### 6.1. `play()` / `stop()`

Параметры `source`, `rule_id` протащить:  
`PlaybackService.play` → `PlaylistManager.play` → `_play_impl` (+ local video path).

### 6.2. Boot resume — schedule-first

Заменить текущий `_resume_playback_after_boot_impl`: сначала `evaluate_now()`, иначе idle (не last manual playlist).

Пересмотреть `_should_resume_playback_after_boot()` — не возобновлять manual playlist при отсутствии активного слота.

### 6.3. Override

`play_override`: читать `source`/`rule_id`/`playlist_id` из PlaybackStatus; писать `previous_*`; `_play_impl` с `source='override'`.

`_maybe_return_after_override`: матрица из §3.  
**Wiring:** `PlaylistManager.set_playback_service(pb)` или callback при `init_services` — в коде **нет** `self.playback_service`.

### 6.4. `get_status()`

`PlaylistManager.get_status()` дополнить `source`, `rule_id` из PlaybackStatus.

`PlaybackService.get_status()` добавить:

```python
status['schedule'] = {
    'source': row.source or 'idle',
    'active_rule_id': row.rule_id,
    'next_rule_at': ...,  # HH:MM или ISO datetime (см. D2.4)
    'next_rule_id': ...,
}
```

**API:** `GET /api/playback/status` → `{ success, status: { ..., schedule: {...} } }`.  
UI badge: `data.status.schedule.source` (не корень ответа).

---

## 7. API Endpoints

Все schedule CRUD: **`@login_required`**.  
`return-to-schedule`: `@login_required` в MVP; Bearer — D2.5.

### 7.1. Schedule CRUD

| Method | Path | Описание |
|--------|------|----------|
| GET | `/api/schedule/rules` | Все не-archived |
| GET | `/api/schedule/week?date=YYYY-MM-DD` | 7 дней instances |
| GET | `/api/schedule/now` | active + next |
| POST | `/api/schedule/rules` | create |
| PUT | `/api/schedule/rules/<id>` | update (partial OK для day transfer) |
| PATCH | `/api/schedule/rules/<id>/archive` | soft delete |
| PATCH | `/api/schedule/rules/<id>/toggle` | enable/disable |
| POST | `/api/playback/return-to-schedule` | manual → schedule |

### 7.2. Валидация

- `start_time < end_time` (строго; **без** полночи).
- `priority` 1–10.
- `days_of_week > 0`.
- `playlist_id` существует.
- `repeat_type == 'once'` → `valid_from` обязателен.
- Парсинг `'HH:MM'` и `'YYYY-MM-DD'` в `_parse_rule_data`.

### 7.3. `GET /week` — instance fields

```json
{
  "id": "12-2026-07-07",
  "rule_id": 12,
  "playlist_id": 3,
  "playlist_name": "...",
  "date": "2026-07-07",
  "day_of_week": 0,
  "days_of_week": 4,
  "repeat_type": "weekly",
  "valid_from": null,
  "valid_until": null,
  "start_time": "11:00",
  "end_time": "14:00",
  "priority": 5,
  "is_active": true,
  "is_expired": false,
  "is_playing_now": false,
  "progress_percent": 0,
  "has_conflict": true
}
```

**Отображение на сетке:**

- `archived_at IS NOT NULL` — **не** включать в `/week`.
- `enabled=false` — **включать** (`is_active: false`), стиль «выключено».
- `is_playing_now` — только если `source=='schedule'`, `rule_id` совпадает, дата = сегодня (по `local_now()`).

### 7.4. D2.1 stub для post-mutate

```python
def _trigger_schedule_evaluate(playback_service):
    engine = getattr(playback_service, '_schedule_engine', None)
    if engine is not None and hasattr(engine, 'evaluate_and_apply'):
        engine.evaluate_and_apply()
```

---

## 8. UI — Интеграция прототипа

### 8.1. Константы сетки (production)

```javascript
const HOUR_START = 0;
const HOUR_END = 23;
const SLOT_HEIGHT = 36;
const MIN_STEP = 15;
const MAGNET_THRESHOLD = 8;
const OVERLAP_THRESHOLD = 4;
```

Прототип (`docs/Расписание протип.html`) — 08:00–22:00, `SLOT_HEIGHT=48`; переносить логику, не константы.

### 8.2. Drag / resize / day transfer

- **Магнит:** до `MAGNET_THRESHOLD` px — click → edit panel.
- **Snap:** 15 мин по Y.
- **Day cross:** `OVERLAP_THRESHOLD` — hint у границы; глубокий заход → commit.
- **Смена дня:** диалог «Перенести / Дублировать / Отменить»:
  - **Перенести** — только если в bitmask ровно один день (`popcount(days_of_week)==1`): XOR bitmask.
  - **Дублировать** — POST нового rule (`once` + `valid_from` = дата целевого дня).
- **Time-only drag:** локальное состояние + unsaved toast → `saveSlot()`.

### 8.3. Прогресс-бар

`is_playing_now` + `progress_percent` с сервера; клиент обновляет каждые 5 с.

### 8.4. Playback badge

Poll `GET /api/playback/status` каждые 5 с:

- `manual` → бейдж + кнопка «Вернуться к расписанию».
- `schedule` / `override` / `idle` — по §3.

### 8.5. CSRF (обязательно)

Все mutating `fetch` из UI:

```javascript
headers: {
  'Content-Type': 'application/json',
  'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
}
credentials: 'include',
```

### 8.6. i18n

Новые строки — `data-i18n` + словарь RU/EN (как `index.js` / `settings.js`).

### 8.7. Из прототипа перенести также

- Empty click по колонке → create slot.
- Context menu: toggle enable, archive.
- Conflict stripe + мягкий dialog при save.
- Priority: 3 уровня label (1–3 / 4–7 / 8–10).
- Toast через `showAlert` (`settings.js`), не `alert()`.

---

## 9. Timezone + NTP в Settings (D2.4)

```python
# settings_service.DEFAULT_SETTINGS
'timezone': 'Europe/Moscow',
'ntp_server': 'pool.ntp.org',
```

UI: select timezone + NTP server + кнопка «Синхронизировать».

**NTP optional:** `ntpdate` + `sudo` может отсутствовать на плеере — кнопка best-effort, не блокер MVP. Acceptance offline 24 ч — **timezone + локальные часы**, без обязательного NTP.

---

## 10. Чеклист фаз

### D2.1 — Модель + API

- [ ] `ScheduleRule` в `dsign/models.py` (или import из submodule)
- [ ] Расширить `PlaybackStatus` (`source`, `rule_id`, `previous_*`)
- [ ] `_ensure_schedule_schema()` в `dsign/extensions.py`
- [ ] Endpoints в `dsign/routes/api/api_routes.py` (или blueprint + `init_routes`)
- [ ] `schedule_time.local_now()` helper
- [ ] Валидация §7.2
- [ ] `GET /week` с полями §7.3 (включая disabled, без archived)
- [ ] `_trigger_schedule_evaluate()` stub
- [ ] `POST /api/playback/return-to-schedule` (stub 501 или no-op до D2.2)
- [ ] **Acceptance:** POST rule → GET rules; invalid `start>=end` → 400

### D2.2 — ScheduleEngine + playback

- [ ] `dsign/services/schedule_engine.py`
- [ ] Singleton на `PlaybackService`; `start()` после app ready
- [ ] `app.app_context()` в tick/evaluate
- [ ] `source`/`rule_id` в `_play_impl`, `_stop_impl`, local video path
- [ ] API play/stop → `source='manual'`
- [ ] `play_override` → `override` + `previous_*`
- [ ] `PlaylistManager.set_playback_service()` + override return matrix
- [ ] Boot schedule-first
- [ ] `get_status()` + block `schedule`
- [ ] `evaluate_and_apply()` после mutate + return-to-schedule
- [ ] Log `schedule_conflict_resolved`
- [ ] (optional) SIGTERM → `engine.stop()`
- [ ] **Acceptance:** 09:00 rule ±30 с; manual blocks schedule; reboot off-slot → idle

### D2.3 — UI: вкладка Расписание

- [ ] `index.html` — вкладки «Плейлисты» | «Расписание»
- [ ] `schedule.css` / `schedule.js` (сетка 00–23)
- [ ] `loadWeek()` → `/api/schedule/week`
- [ ] Drag: magnets + day dialog (transfer/duplicate/cancel)
- [ ] Resize: bottom handle, snap 15 мин
- [ ] Progress bar для `is_playing_now`
- [ ] `saveSlot()` → PUT/POST; conflict warning
- [ ] Empty click → create
- [ ] Context menu: toggle, archive
- [ ] Sidebar: source badge + return button
- [ ] CSRF на все POST/PATCH
- [ ] i18n ключи для UI расписания
- [ ] **Acceptance:** drag/save survives reload; day transfer → dialog

### D2.4 — Polish

- [ ] Timezone в Settings UI
- [ ] NTP sync (optional, best-effort)
- [ ] Monthly hidden в repeat select
- [ ] Offline 24 ч test
- [ ] **Acceptance:** чеклист `docs/dsign_4phase_checklist.md` D2

### D2.5 — v2 (не MVP)

- [ ] Monthly repeat + month view
- [ ] Исключения (отменить одну среду)
- [ ] Fleet Bearer на schedule API
- [ ] Batch save
- [ ] Touch / keyboard

---

## 11. Зависимости (выполнены)

| Фаза | Статус |
|------|--------|
| B3 override | ✅ |
| B4 Bearer play/stop | ✅ |
| B5 pause/seek/skip | ✅ |

---

## 12. Prompt template (D2.1)

```
Implement D2.1: Schedule model and API for dsign.

Repo facts:
- Flask + SQLite, NO Alembic
- Models: dsign/models.py
- Migrations: dsign/extensions.py (_ensure_schedule_schema)
- API: dsign/routes/api/api_routes.py, @login_required
- Time: dsign/services/schedule_time.py (zoneinfo)

Deliverables: ScheduleRule, PlaybackStatus columns, CRUD + /week + /now,
validation, _trigger_schedule_evaluate stub.

Do NOT implement ScheduleEngine (D2.2) or UI (D2.3).
```

---

*End of spec v1.1.*
