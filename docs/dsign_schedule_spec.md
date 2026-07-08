# D2 Schedule Engine — Спецификация

**Версия:** 1.5 (D2.1–D2.5 реализовано)  
**Статус:** D2 завершён (PR #103–#107); offline 24 ч 🟡; fleet Bearer — настроить `DSIGN_API_TOKEN` на плеере  
**Репозиторий:** `kirpodolak/dsign` (Flask + SQLite + MPV + Wayland/labwc)  
**Прототип UI:** `docs/Расписание протип.html` (demo 08:00–22:00; production — 00:00–23:59)

### Changelog 1.4 → 1.5

- **D2.1–D2.5:** реализовано и проверено на плеере (CRUD, engine, UI, timezone/NTP, month view, exceptions, batch API).
- **§10:** чеклисты D2.1–D2.3, D2.5 — [x]; D2.4 — offline 24 ч остаётся 🟡.
- **§7:** schedule API — `@api_session_or_token_required` (session + Fleet Bearer); добавлены `/month`, `/exceptions`, `/rules/batch`.
- **Monthly repeat:** перенесён из «hidden в D2.4» в D2.5 (UI + `repeat_type: monthly`).

### Changelog 1.3 → 1.4

- **`handle_override_return`:** guard `if row is None` и `if self._schedule_engine is None` — handler регистрируется в `init_services()` раньше, чем engine создаётся в `_init_background_loop()`.
- **`PlaybackService.__init__` (D2.2):** `self._schedule_engine = None` до фоновой инициализации.

### Changelog 1.2 → 1.3

- **`evaluate_now()` vs `evaluate_and_apply()`:** разделены — `evaluate_now()` → `Optional[ScheduleRule]` без side effects; `evaluate_and_apply()` → `_evaluate()` с play/stop. Boot resume, override return, `return-to-schedule` используют **`evaluate_and_apply()`**.
- **Wiring:** `set_override_return_handler` в `init_services()` (wire-блок); `ScheduleEngine` создаётся и `start()` в `PlaybackService._init_background_loop()` после `set_app`.
- **`_trigger_schedule_evaluate`:** `playback_service` из closure `init_api_routes(api_bp, services)` (не `current_app.playback_service`).
- **`GET /week`:** `if playback is None: is_playing_now = False`.
- **`GET /now` / `_find_next_rule`:** сравнение через `datetime.time.min`, не `datetime.min.time()`.
- **NTP:** уточнены force-sync шаги (`systemctl restart systemd-timesyncd`, `chronyc makestep`).

### Changelog 1.1 → 1.2

- **PlaybackStatus:** уточнено по коду — запись `playlist_id`/`status`/`source`/`rule_id` только в `PlaylistManager` (`_play_impl`, `_play_local_video_engine`, `_stop_impl`); `PlaybackService.play()`/`stop()` — делегаты без записи в БД.
- **Wiring:** `PlaylistManager.set_override_return_handler()` (не `set_playback_service`); callback из `init_services` → `PlaybackService.handle_override_return`.
- **Override return:** при `previous_source=='schedule'` всегда `evaluate_now()` (актуальный слот), не replay `previous_rule_id`.
- **`PlaybackStatus.timestamp`:** legacy Unix-секунды при создании строки; при play/stop сегодня не обновляется.
- **`GET /week`:** комментарий — `days_of_week`/`repeat_type` в instance для UI day-transfer (`popcount`).
- **`is_playing_now`:** явно `now = local_now(settings_service)`.
- **`evaluate_and_apply()`:** публичная обёртка над `_evaluate()`.
- **`return-to-schedule`:** убран из D2.1; endpoint только в D2.2.
- **SIGTERM:** комментарий про `python run.py` + systemd; MVP — `daemon=True` timer.
- **NTP:** цепочка `timedatectl` → `chronyc` → `ntpdate` (legacy).

### Changelog 1.0 → 1.1

- Пути файлов приведены к реальному layout репозитория (`extensions.py`, `routes/api/`).
- Inline-миграция — по образцу `_ensure_playlist_sort_order_column`.
- `GET /week`: поля для day-transfer UI, disabled rules на сетке, timezone через `local_now()`.
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
│  └─ /api/playback/return-to-schedule (D2.2+)              │
├─────────────────────────────────────────────────────────────┤
│  ScheduleEngine (singleton, daemon timer, 30 с)            │
│  ├─ _evaluate() → find active rule → play/stop             │
│  ├─ evaluate_now() — read-only active rule                       │
│  ├─ evaluate_and_apply() — _evaluate() + play/stop               │
│  └─ local_now() — timezone-aware (settings.timezone)           │
├─────────────────────────────────────────────────────────────┤
│  PlaybackService (оркестрация, без записи PlaybackStatus)  │
│  ├─ play(playlist_id, source, rule_id) → PlaylistManager   │
│  ├─ stop(source) → PlaylistManager                        │
│  ├─ play_override() → previous_* + override play            │
│  ├─ handle_override_return() — матрица §3                  │
│  └─ boot resume: schedule-first (evaluate_and_apply)        │
├─────────────────────────────────────────────────────────────┤
│  PlaylistManager (MPV + единственная запись PlaybackStatus)  │
│  ├─ play/stop → _play_impl / _stop_impl / local video path │
│  ├─ on_override_return callback → PlaybackService         │
│  └─ _maybe_return_after_override → callback (не _play_impl)│
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

### 1.2. Кто пишет PlaybackStatus (факт кода, v1.2)

| Поле | Где пишется сегодня | После D2 |
|------|---------------------|----------|
| `playlist_id`, `status` | `PlaylistManager._play_impl`, `_play_local_video_engine`, `_stop_impl` | то же |
| `source`, `rule_id` | — (новые колонки) | **там же**, в том же `commit` |
| `previous_*` | — | `PlaybackService.play_override()` **до** вызова `PlaylistManager.play_override` |
| `timestamp` | default при `PlaybackStatus(id=1)` create | не трогать в MVP (см. §2.2) |

`PlaybackService.play()` / `stop()` — только логирование и делегирование; **не** `db.session.commit()` для `PlaybackStatus`.

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

    repeat_type  = Column(String(16), default='weekly')   # weekly | once | monthly
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
    timestamp    = Column(Integer, default=lambda: int(time.time()))  # legacy — см. ниже

    source              = Column(String(16), default='idle')  # manual|schedule|override|idle
    rule_id             = Column(Integer, ForeignKey('schedule_rules.id'), nullable=True)
    previous_source     = Column(String(16), nullable=True)
    previous_rule_id    = Column(Integer, ForeignKey('schedule_rules.id'), nullable=True)
    previous_playlist_id = Column(Integer, ForeignKey('playlists.id'), nullable=True)
```

**`timestamp` (existing):**

- Unix time (секунды), выставляется **только** при создании строки (`default=`).
- При play/stop в текущем коде **не обновляется**; используется в `to_dict()` для API.
- Schedule **не зависит** от `timestamp`. `to_dict()` может отдавать устаревшее время — legacy, не блокер MVP. Опционально D2.4: обновлять при смене `status`.

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
- Manual play блокирует расписание до Stop или `POST /api/playback/return-to-schedule` (D2.2+).
- `play_override()` выставляет `previous_*` в **PlaybackStatus** (и `_override_return_ctx` для `item_index`); play с `source='override'`.
- **Override return** (через `handle_override_return`, не прямой `_play_impl`):
  - `previous_source == 'schedule'` → **`evaluate_and_apply()`** → play активного слота или idle (**не** replay `previous_rule_id` — время могло смениться за override);
  - `previous_source == 'manual'` → `play(previous_playlist_id, source='manual')`;
  - иначе → `stop(source='schedule')` → idle.

**Propagation `source` / `rule_id`:**

| Entry point | source | rule_id |
|-------------|--------|---------|
| `POST /api/playback/play` | `manual` | `None` |
| `POST /api/playback/stop` | → status `stopped`; source остаётся или `manual` | — |
| `play_override()` | `override` | `None` |
| `ScheduleEngine._evaluate` | `schedule` | `active_rule.id` |
| Конец слота / нет правила | `stop(source='schedule')` → `idle` | `None` |

**Цепочка вызовов (сигнатуры):**

```python
# PlaybackService — протащить kwargs, без commit PlaybackStatus
def play(self, playlist_id, *, source='manual', rule_id=None, start_index=0, ...):
    return self._playlist_manager.play(
        playlist_id, source=source, rule_id=rule_id, start_index=start_index, ...
    )

def stop(self, *, source='manual', ...):
    return self._playlist_manager.stop(source=source, ...)

# PlaylistManager — записать в БД в _play_impl / _play_local_video_engine / _stop_impl
def play(self, playlist_id, *, source='manual', rule_id=None, ...):
    return self._play_impl(playlist_id, source=source, rule_id=rule_id, ...)

def _play_impl(..., source='manual', rule_id=None):
    ...
    playback.source = source
    playback.rule_id = rule_id
    playback.playlist_id = playlist_id
    playback.status = 'playing'
    db.session.commit()
```

Запись **`playlist_id` / `status` / `source` / `rule_id`** — в **`PlaylistManager._play_impl`**, **`_play_local_video_engine`**, **`_stop_impl`** (один `commit` на операцию).

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

- Engine и **все** schedule API (`/week`, `/now`, `is_playing_now`) используют **один** helper — **`local_now()`**, не `datetime.now()` naive.
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
- **Graceful shutdown:** `digital-signage.service` запускает `python run.py` (Flask dev server). При `systemctl stop` процесс получает SIGTERM; отдельного shutdown hook в проекте **нет**. Для MVP: `daemon=True` timer завершится с процессом — **acceptable**. Явный `SIGTERM → engine.stop()` — **D2.4 / v2** (не блокер).

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
            # Уже играет тот же rule + playlist — не рестартить
            if (
                current_rule_id == active_rule.id
                and current_playlist == active_rule.playlist_id
            ):
                return
            self.playback.play(
                active_rule.playlist_id,
                source='schedule',
                rule_id=active_rule.id,
            )
        else:
            if self._get_current_source() == 'schedule':
                self.playback.stop(source='schedule')
```

### 5.3. `_find_active_rule`

Фильтры: `enabled`, `archived_at IS NULL`, bitmask дня, `start_time <= now.time() < end_time` (end exclusive), weekly/once, `valid_until`.

Сортировка: `priority ASC`, `id ASC`. Победитель — `rules[0]`.

**Лог `schedule_conflict_resolved`:** если кандидатов > 1, INFO с `chosen_rule_id`, `skipped_rule_ids`, `now`.

### 5.4. `evaluate_now()` / `evaluate_and_apply()`

```python
def evaluate_now(self) -> Optional[ScheduleRule]:
    """Read-only: active rule at local_now. No play/stop side effects."""
    with self._app.app_context():
        return self._find_active_rule(local_now(self.settings_service))

def evaluate_and_apply(self) -> None:
    """Public entry: evaluate schedule and apply play/stop (side effects)."""
    with self._app.app_context():
        self._evaluate()
```

| Событие | Действие |
|---------|----------|
| D2.1 API mutate | stub / no-op (engine ещё нет) |
| D2.2+ POST/PUT/toggle/archive | `engine.evaluate_and_apply()` |
| Boot resume | `engine.evaluate_and_apply()` (не `evaluate_now`) |
| Override return (`previous_source=='schedule'`) | `evaluate_and_apply()` |
| `return-to-schedule` (D2.2+) | `evaluate_and_apply()` |
| `GET /api/schedule/now` (peek active) | `evaluate_now()` |

### 5.5. `_find_next_rule` (для `/now`, D2.1+)

При поиске следующего слота сегодня/в будущем — сравнение `Time` только с **naive** `datetime.time`:

```python
from datetime import time as dt_time

def _time_cutoff(now, *, same_day: bool) -> dt_time:
    return now.time() if same_day else dt_time.min  # НЕ datetime.min.time()
```

`local_now()` возвращает **aware** datetime; для SQLAlchemy `Time` и Python-сравнений использовать только `.time()` (naive) или `dt_time.min`.

---

## 6. PlaybackService / PlaylistManager

### 6.1. `play()` / `stop()` — делегирование + kwargs

Параметры `source`, `rule_id` протащить по цепочке:

`PlaybackService.play` → `PlaylistManager.play` → `_play_impl`  
`PlaybackService.play` → `PlaylistManager.play` → `_play_local_video_engine` (local video path)  
`PlaybackService.stop` → `PlaylistManager.stop` → `_stop_impl`

`PlaybackService` **не** делает отдельный `commit` для `PlaybackStatus`.

### 6.2. Boot resume — schedule-first

Заменить `_resume_playback_after_boot_impl`: вызвать **`self._schedule_engine.evaluate_and_apply()`** (D2.2+), иначе idle. **Не** восстанавливать last manual playlist из `PlaybackStatus.playlist_id`.

`evaluate_now()` здесь **не** использовать — он без side effects.

Пересмотреть `_should_resume_playback_after_boot()` — не возобновлять manual playlist при отсутствии активного слота.

### 6.3. Override + wiring

**`play_override` (PlaybackService):**

1. Прочитать `source` / `rule_id` / `playlist_id` из `PlaybackStatus`.
2. Записать `previous_source`, `previous_rule_id`, `previous_playlist_id` (+ `item_index` в `_override_return_ctx` как сейчас).
3. Вызвать `PlaylistManager.play_override(...)` → `_play_impl(..., source='override', rule_id=None)`.

**`_maybe_return_after_override` (PlaylistManager):**

- **Не** вызывать `_play_impl` напрямую для schedule-return.
- Вызвать `self._on_override_return()` если задан.

**Wiring (D2.2):**

1. **`init_services()`** — после создания `playback_service` (wire-блок, рядом с ContentCache / SettingsService):

```python
# dsign/services/__init__.py — init_services()
pb = services.get("playback_service")
if pb:
    pm = getattr(pb, "_playlist_manager", None)
    if pm is not None and hasattr(pm, "set_override_return_handler"):
        pm.set_override_return_handler(pb.handle_override_return)
```

Handler регистрируется в `init_services()` **до** создания `ScheduleEngine` в `_init_background_loop()`. Поэтому `handle_override_return` **обязан** проверять `self._schedule_engine is not None` (редкий gap между стартом Flask и фоновым init — override до engine).

2. **`PlaybackService._init_background_loop()`** — после `set_app` / `app.app_context()`:

```python
self._schedule_engine = ScheduleEngine(self, settings_service=...)
self._schedule_engine.start()  # daemon timer
```

В `PlaybackService.__init__` (D2.2): `self._schedule_engine = None`.

`PlaylistManager` создаётся в `PlaybackService.__init__` **раньше** engine — handler вешается из `init_services`, не из `__init__`.

D2.1: поле `_on_override_return = None` (stub); без handler — legacy B3 `_play_impl` return.

```python
# PlaybackService (D2.2)
def handle_override_return(self) -> None:
    with self._app_context():
        row = self.db_session.query(PlaybackStatus).get(1)
        if row is None:
            self.stop(source='schedule')
            return

        prev = row.previous_source
        if prev == 'schedule':
            if self._schedule_engine is not None:
                self._schedule_engine.evaluate_and_apply()
            else:
                # Engine not ready yet — safe fallback
                self.stop(source='schedule')
        elif prev == 'manual' and row.previous_playlist_id:
            self.play(row.previous_playlist_id, source='manual')
        else:
            self.stop(source='schedule')
```

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

Все schedule endpoints: **`@api_session_or_token_required`** (Flask-Login session + `DSIGN_API_TOKEN` Bearer с D2.5).

### 7.1. Schedule CRUD

| Method | Path | Описание | Фаза |
|--------|------|----------|------|
| GET | `/api/schedule/rules` | Все не-archived | D2.1 |
| GET | `/api/schedule/week?date=YYYY-MM-DD` | 7 дней instances | D2.1 |
| GET | `/api/schedule/month?date=YYYY-MM-DD` | календарный месяц instances | D2.5 |
| GET | `/api/schedule/now` | active + next | D2.1 |
| POST | `/api/schedule/rules` | create | D2.1 |
| POST | `/api/schedule/rules/batch` | batch create/update/archive | D2.5 |
| PUT | `/api/schedule/rules/<id>` | update (partial OK для day transfer) | D2.1 |
| PATCH | `/api/schedule/rules/<id>/archive` | soft delete | D2.1 |
| PATCH | `/api/schedule/rules/<id>/toggle` | enable/disable | D2.1 |
| POST | `/api/schedule/exceptions` | skip one occurrence `{rule_id, date}` | D2.5 |
| DELETE | `/api/schedule/exceptions` | remove skip `{rule_id, date}` | D2.5 |
| POST | `/api/playback/return-to-schedule` | manual → schedule | D2.2 |

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

**`days_of_week` / `repeat_type` в instance:** копируются из **rule** (не свойство инстанса). Нужны UI для day-transfer: `popcount(days_of_week)==1` → диалог «Перенести»; иначе только «Дублировать».

**Отображение на сетке:**

- `archived_at IS NOT NULL` — **не** включать в `/week`.
- `enabled=false` — **включать** (`is_active: false`), стиль «выключено».
- `is_playing_now` — только если текущий playback `source=='schedule'`, `rule_id` совпадает, **`instance.date == local_now(settings_service).date()`** (для прошлой/будущей недели всегда `false`).

```python
now = local_now(settings_service)
playback = PlaybackStatus.query.get(1)  # или .first()
is_playing_now = False
if playback is not None:
    is_playing_now = (
        playback.source == 'schedule'
        and playback.rule_id == rule.id
        and instance_date == now.date()
    )
```

### 7.4. D2.1 stub для post-mutate

`playback_service` доступен в API через **closure** при регистрации маршрутов (как сегодня):

```python
# dsign/routes/api/api_routes.py
def init_api_routes(api_bp, services):
    playback_service = services.get('playback_service')
    ...

def _trigger_schedule_evaluate():
    # вызывать из route handler внутри init_api_routes — playback_service в closure
    engine = getattr(playback_service, '_schedule_engine', None)
    if engine is not None and hasattr(engine, 'evaluate_and_apply'):
        engine.evaluate_and_apply()
```

**Нет** `current_app.playback_service` — не добавлять без отдельного `app.extensions`.

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

- `manual` → бейдж + кнопка «Вернуться к расписанию» (disabled до D2.2, если endpoint нет).
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

**NTP sync (best-effort, не блокер MVP):**

```python
# Одноразовый force sync (не только «включить NTP»):
# 1. timedatectl status                    — проверка (уже sync?)
# 2. systemctl restart systemd-timesyncd   — force sync (Ubuntu/RPi)
# 3. chronyc -a makestep                 — если chrony установлен
# 4. ntpdate -u <server>                 — legacy, может отсутствовать
```

`timedatectl set-ntp true` — **включение** автосинхронизации, не разовый шаг.

Кнопка не должна падать, если `sudo` / `ntpdate` недоступны. Acceptance offline 24 ч — **timezone + локальные часы**, без обязательного NTP.

---

## 10. Чеклист фаз

### D2.1 — Модель + API

- [x] `ScheduleRule` в `dsign/models.py` (или import из submodule)
- [x] Расширить `PlaybackStatus` (`source`, `rule_id`, `previous_*`)
- [x] `_ensure_schedule_schema()` в `dsign/extensions.py`
- [x] Endpoints в `dsign/routes/api/api_routes.py` (кроме `return-to-schedule`)
- [x] `schedule_time.local_now()` helper
- [x] Валидация §7.2
- [x] `GET /week` с полями §7.3 (включая disabled, без archived; `days_of_week` для day-transfer)
- [x] `_trigger_schedule_evaluate()` stub
- [x] `PlaylistManager._on_override_return = None` (поле stub; без handler — legacy B3 `_play_impl` return)
- [x] **Acceptance:** POST rule → GET rules; invalid `start>=end` → 400

### D2.2 — ScheduleEngine + playback

- [x] `dsign/services/schedule_engine.py`
- [x] Singleton на `PlaybackService`; `start()` после app ready
- [x] `evaluate_now()` (read-only) / `evaluate_and_apply()` (side effects)
- [x] `app.app_context()` в tick/evaluate
- [x] `source`/`rule_id` kwargs: `PlaybackService` → `PlaylistManager` → `_play_impl` / `_play_local_video_engine` / `_stop_impl`
- [x] API play/stop → `source='manual'`
- [x] `play_override` → `override` + `previous_*` в PlaybackStatus
- [x] `handle_override_return` с guard `row is None` / `_schedule_engine is None` (§6.3)
- [x] Boot schedule-first (`evaluate_and_apply`, не `evaluate_now`)
- [x] `get_status()` + block `schedule`
- [x] `POST /api/playback/return-to-schedule`
- [x] `evaluate_and_apply()` после mutate + return-to-schedule
- [x] Log `schedule_conflict_resolved`
- [x] (optional D2.4) SIGTERM → `engine.stop()`
- [x] **Acceptance:** 09:00 rule ±30 с; manual blocks schedule; reboot off-slot → idle

### D2.3 — UI: вкладка Расписание

- [x] `index.html` — вкладки «Плейлисты» | «Расписание»
- [x] `schedule.css` / `schedule.js` (сетка 00–23)
- [x] `loadWeek()` → `/api/schedule/week`
- [x] Drag: magnets + day dialog (transfer/duplicate/cancel)
- [x] Resize: bottom handle, snap 15 мин
- [x] Progress bar для `is_playing_now`
- [x] `saveSlot()` → PUT/POST; conflict warning
- [x] Empty click → create
- [x] Context menu: toggle, archive
- [x] Sidebar: source badge + return button
- [x] CSRF на все POST/PATCH
- [x] i18n ключи для UI расписания
- [x] **Acceptance:** drag/save survives reload; day transfer → dialog

### D2.4 — Polish

- [x] Timezone в Settings UI
- [x] NTP sync (optional, best-effort; §9 fallback chain)
- [x] ~~Monthly hidden в repeat select~~ → перенесено в D2.5 (monthly включён)
- [ ] Offline 24 ч test 🟡
- [x] (optional) SIGTERM shutdown hook
- [x] **Acceptance:** чеклист `docs/dsign_4phase_checklist.md` D2 (кроме offline 24 ч)

### D2.5 — Расширения (завершено)

- [x] Monthly repeat + month view
- [x] Исключения (отменить одну среду)
- [x] Fleet Bearer на schedule API (код; на плеере задать `DSIGN_API_TOKEN`)
- [x] Batch save (`POST /schedule/rules/batch`)
- [x] Touch / keyboard

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
- PlaybackStatus DB writes: PlaylistManager only (not PlaybackService.play)
- Do NOT register POST /api/playback/return-to-schedule (D2.2)

Deliverables: ScheduleRule, PlaybackStatus columns, CRUD + /week + /now,
validation, _trigger_schedule_evaluate stub, _on_override_return stub field.

Do NOT implement ScheduleEngine (D2.2) or UI (D2.3).
```

---

*End of spec v1.5.*
