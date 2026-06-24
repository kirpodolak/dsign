[dsign_4phase_checklist.md](https://github.com/user-attachments/files/29295367/dsign_4phase_checklist.md)

# DSign — План развития: от хорошего плеера к референсному
# 4 фазы: A → C → B → D (Playback → Content → API → Ops)
# Формат: Что меняем → Зачем → Какой результат
# Транслируемо в Cursor prompts

---

## Фаза A — "Ноль чёрных кадров" (Playback Engine)
**Цель:** Плеер никогда не показывает чёрный экран. Любой контент переходит в любой контент без gap.

---

### A1. Local Video Playlist → MPV Internal Playlist
**Что меняем:**
- В `PlaylistManager.play()` добавляем ветвление: если все items — локальные видео, идем в новую ветку `play_local_video_playlist()`.
- Генерируем M3U/FFconcat плейлист и скармливаем MPV одним `loadfile`.
- Включаем `prefetch-playlist=yes` в mpv.conf.

**Зачем:**
- Сейчас: каждый переход = `loadfile replace` = decoder останавливается, новый стартуется = 50-500ms чёрный экран.
- MPV internal playlist: decoder не останавливается, следующий файл предзагружается в фоне. Переход = switch buffer = 0ms.
- Это стандартный механизм MPV, который мы не используем.

**Логика работы:**
```
Плеер видит: [video1.mp4, video2.mp4, video3.mp4] — все локальные видео.
Вместо: loadfile video1 → ждём EOF → loadfile video2 → ждём EOF → loadfile video3
Делаем:  loadfile playlist.m3u → MPV сам играет video1, предзагружает video2, switch на EOF
Мониторим: playlist-pos property = какой сейчас item (0, 1, 2...)
```

**Результат:**
- Video → video: zero gap (Grade A, < 50ms)
- CPU: ниже (не пересоздаём decoder)
- Memory: стабильнее (не аллоцируем/деаллоцируем каждый раз)

**Acceptance:**
- 10 циклов [A, B] — 10/10 zero gap
- `journalctl` — только 1 `loadfile` на весь плейлист

---

### A2. Single Video → loop-file=inf
**Что меняем:**
- Если плейлист = 1 локальное видео: `set_property loop-file inf` + один `loadfile`.
- Не используем outer while + reload.

**Зачем:**
- Сейчас: каждый цикл = новый `loadfile replace` = gap + CPU spike.
- `loop-file=inf`: MPV сам loop'ит внутри, decoder живёт, gap = 0.

**Логика работы:**
```
Плеер видит: [one_video.mp4] — один файл.
Вместо: while True: loadfile one_video → EOF → loadfile one_video → EOF...
Делаем:  set loop-file inf → loadfile one_video → MPV сам loop'ит
Мониторим: loop-count property (сколько раз проиграл)
```

**Результат:**
- Loop point: zero gap
- CPU: flat line (нет spike при reload)
- SD card: меньше wear (не читаем файл заново)

**Acceptance:**
- 5 минут loop — 0 `loadfile` в логах (кроме первого)
- Memory: не растёт

---

### A3. Safe Loadfile — валидация перед playback
**Что меняем:**
- Новый метод `_safe_loadfile(path)`:
  1. `os.path.exists()` — файл на месте?
  2. `ffprobe -v error` — файл валидный? (быстро, 100-500ms)
  3. `loadfile` — загрузить
  4. Ждём `vo-configured=true` (max 5 сек) — MPV реально показывает кадры?
  5. Если fail → skip, log, next item

**Зачем:**
- Сейчас: corrupt/missing file → MPV может hang, плеер умирает.
- Safe loadfile: fail fast, graceful degradation.

**Логика работы:**
```
Плеер готовит video_X.mp4:
  1. Файл есть? Нет → skip, log "missing: video_X.mp4"
  2. ffprobe говорит "valid"? Нет → skip, log "corrupt: video_X.mp4"
  3. loadfile → MPV отвечает "success"? Нет → skip, log "load failed"
  4. Ждём 5 сек: vo-configured? Нет → skip, log "VO timeout"
  5. Всё ок → playback started
На любом шаге fail: показываем logo, идём к следующему item
```

**Результат:**
- Corrupt file: skip за < 2 сек, не hang
- Missing file: skip за < 1 сек
- Logo visible во время recovery (не чёрный экран)

**Acceptance:**
- Плейлист [valid, corrupt, valid] — играет valid, skip corrupt, играет valid
- Плеер не умирает, не требует restart

---

### A4. video-sync=audio в Wayland-профиле
**Что меняем:**
- В `etc/dsign/mpv-minimal/profiles/` и `dsign-mpv-launch-wayland`:
  - Убрать `video-sync=display-vdrop`
  - Поставить `video-sync=audio` (или `display-resample`)

**Зачем:**
- `display-vdrop`: при 60Hz + 29.97fps = judder (прыгающие кадры).
- `audio`: синхронизация по аудио, плавное воспроизведение.
- Уже проверено на железе, но не зафиксировано в репо.

**Логика работы:**
```
MPV может синхронизироваться:
  - display-vdrop: выкидывать/дублировать кадры под частоту экрана
    → judder на 29.97fps @ 60Hz
  - audio: синхронизация по аудио clock
    → плавно, без judder
  - display-resample: ресемплинг аудио под видео
    → тоже плавно, но дороже CPU
Для signage: audio sync оптимально (контент обычно 24/30fps, экран 60Hz)
```

**Результат:**
- Плавное воспроизведение без judder
- CPU: не выше, чем с display-vdrop

**Acceptance:**
- 29.97fps видео на 60Hz экране — нет judder (глаз/камера)
- 24fps видео — нет stutter

---

### A5. Tech Debt (быстрые фиксы)
**Что меняем:**
- `wayland_manger.py` → `wayland_manager.py` (typo в имени файла)
- Удалить/починить `restart_mpv()` dead code в `playlist_management.py`
- `_log_debug` → `logger.debug` в `playback_service.py`

**Зачем:**
- ImportError на чистом деплое
- Путаница при отладке (мертвый код)
- Runtime exception в `get_status`

**Результат:**
- Clean deploy без ручных правок
- Стабильная разработка

---

## Фаза C — Content Resilience (Offline + Formats)
**Цель:** Плеер выживает без интернета. Поддерживает больше форматов.

---

### C1. ContentCache — Preload следующих N файлов
**Что меняем:**
- Новый модуль `content_cache.py`:
  - `ensure_cached(url)` — скачать если не в кэше
  - `preload_upcoming(items, N)` — предзагрузить следующие N файлов
  - `cleanup_lru(max_size_gb)` — удалить старые, если переполнено
- Интеграция в `PlaylistManager`: перед стартом item N+1 — начать качать item N+2

**Зачем:**
- Сейчас: каждый item грузится в момент playback = latency, stall.
- Preload: следующий файл уже локально = instant switch.
- Offline: если интернет пропал, кэшированные файлы играют.

**Логика работы:**
```
Плейлист: [video1.mp4, video2.mp4, video3.mp4]
Время:    t=0       t=10      t=20

t=0:  начинаем video1, фоном качаем video2
t=5:  video2 скачан, кэш hit
t=10: переход video1→video2 (instant, файл локальный)
      фоном качаем video3
t=20: переход video2→video3 (instant)

Если интернет пропал в t=15:
  video3 ещё не скачан → показываем logo
  video1 и video2 в кэше → можно loop'ить их
```

**Результат:**
- Network → local: instant switch
- Offline: fallback to cached content
- SD wear: контролируем (LRU cleanup, max size)

**Acceptance:**
- Плейлист [net1, net2, net3] — net2 предзагружен до окончания net1
- Отключение интернета — playback cached files, не чёрный экран

---

### C2. Audio-Only + Logo
**Что меняем:**
- Если item = audio-only (нет video track): MPV играет аудио, imv показывает logo.
- Или: MPV + `--force-window=immediate` + logo как background.

**Зачем:**
- Фоновая музыка в signage — частый кейс.
- Сейчас: audio-only = чёрный экран (MPV без видео).

**Логика работы:**
```
Плеер видит: audio.mp3 (нет video track)
Вместо: чёрный экран
Делаем:  MPV играет audio.mp3
         imv (или MPV force-window) показывает idle_logo.jpg
         Logo + музыка = полноценный signage
```

**Результат:**
- Audio-only контент = logo + музыка
- Новый формат без нового engine

**Acceptance:**
- Плейлист [video.mp4, audio.mp3, video2.mp4] — audio играет с logo

---

### C3. Nested Playlists (Playlists inside playlists)
**Что меняем:**
- `Playlist` model: `items` может содержать `PlaylistItem` или `PlaylistReference`.
- Резолвинг: при загрузке плейлиста — раскрыть nested playlists в flat list.

**Зачем:**
- "Утренний блок" = [новости, погода, реклама] — reusable компонент.
- Не дублировать одни и те же sequence в разных плейлистах.

**Логика работы:**
```
Playlist "Day":
  - 09:00-12:00: "Morning Block" (nested playlist)
    - [news.mp4, weather.mp4, ads.mp4]
  - 12:00-15:00: "Afternoon Block"
    - [music.mp3, promo.mp4]

Раскрываем в flat:
  [news.mp4, weather.mp4, ads.mp4, music.mp3, promo.mp4]
Играем как обычно.
```

**Результат:**
- Reusable blocks
- Меньше дублирования
- Проще scheduling

---

## Фаза B — API для Управления (Remote Control)
**Цель:** Внешняя система (облако, панель, скрипт) может полностью управлять плеером.

---

### B1. Расширить GET /api/playback/status
**Что меняем:**
- Добавить поля:
  - `time_pos` — текущая позиция (сек)
  - `duration` — длительность текущего item
  - `item_index` — индекс в плейлисте
  - `item_count` — всего items
  - `media_key` — ключ текущего media
  - `mpv_responsive` — bool, отвечает ли IPC
  - `is_network` — bool, сетевой ли поток
  - `cache_state` — сколько предзагружено

**Зачем:**
- Сейчас: status = "playing", playlist_id, current_media. Мало.
- Облаку нужно знать: где мы, сколько осталось, жив ли MPV.

**Логика работы:**
```
GET /api/playback/status →
{
  "status": "playing",
  "playlist_id": 42,
  "playlist_name": "Morning",
  "item_index": 3,
  "item_count": 10,
  "current_media": "promo_v2.mp4",
  "media_key": "promo_v2.mp4",
  "time_pos": 12.5,
  "duration": 30.0,
  "remaining": 17.5,
  "is_network": false,
  "mpv_responsive": true,
  "cache_state": {
    "cached_items": 2,
    "total_items": 10,
    "cache_size_mb": 150
  }
}
```

**Результат:**
- Облако видит полную картину
- Можно строить analytics, monitoring

---

### B2. GET /api/health — Aggregated Status
**Что меняем:**
- Новый endpoint, агрегирующий всё:
  - MPV health (IPC ping, version, vo-configured)
  - System health (CPU, RAM, disk, temp, uptime)
  - Display health (resolution, HDMI connected, EDID)
  - Network health (IP, gateway, DNS, latency)
  - Cache health (size, hit rate)

**Зачем:**
- Одним запросом — полная диагностика.
- Monitoring systems (Prometheus, Zabbix) — один endpoint.

**Логика работы:**
```
GET /api/health →
{
  "status": "healthy",  // healthy | degraded | critical
  "timestamp": "2026-06-23T20:00:00Z",
  "checks": {
    "mpv": {"status": "ok", "version": "0.38.0", "responsive": true},
    "system": {"cpu_percent": 15, "ram_percent": 45, "temp_c": 52},
    "display": {"resolution": "1920x1080", "hdmi_connected": true},
    "network": {"ip": "192.168.1.100", "gateway_reachable": true},
    "cache": {"size_mb": 150, "max_mb": 1024}
  }
}
```

**Результат:**
- One-call diagnostics
- Automated monitoring ready

---

### B3. POST /api/playback/override — Emergency Content
**Что меняем:**
- Новый endpoint:
  - `playlist_id` — что играть
  - `duration` — сколько секунд (или `null` = до cancel)
  - `priority` — 1 = highest
  - `return_to_previous` — bool

**Зачем:**
- Emergency alert: "Пожар!" — мгновенно прерывает всё.
- Ad insertion: "Реклама на 30 сек" — потом возвращаемся.

**Логика работы:**
```
Сейчас играет: "Morning Show" (playlist 1)
Приходит: POST /api/playback/override
  { "playlist_id": 99, "duration": 30, "return_to_previous": true }

Плеер:
  1. Сохраняет текущий state (playlist 1, item 3, time_pos 45)
  2. Сразу (cut) стартует playlist 99
  3. Ждёт 30 сек (или EOF)
  4. Возвращается к playlist 1, item 3, seek 45
  5. Продолжает как будто не останавливался
```

**Результат:**
- Emergency: < 1 сек latency
- Ad insertion: seamless return
- Priority system: high-priority override low-priority

**Acceptance:**
- Override latency < 3 сек
- Auto-return работает
- Нет чёрного экрана при override

---

### B4. API Token (Headless Auth)
**Что меняем:**
- Новый auth method: `Authorization: Bearer <token>`
- Token = static key из env (`DSIGN_API_TOKEN`)
- UI остаётся на session cookie

**Зачем:**
- Сейчас: только session cookie = нужен браузер, CSRF, login flow.
- Headless control (скрипты, облако): Bearer token проще.

**Логика работы:**
```
UI: login → cookie session (как сейчас)
API: curl -H "Authorization: Bearer $DSIGN_API_TOKEN"         http://pi:5000/api/playback/start

Token хранится в env, генерируется при install.
Можно rotate: `dsign-token rotate`
```

**Результат:**
- Скрипты и облако могут управлять без браузера
- UI не ломается

---

### B5. WebSocket playback_command — доделать или REST
**Что меняем:**
- Либо доделать WebSocket `playback_command` (сейчас stub)
- Либо отказаться от WS и делать всё через REST + polling

**Зачем:**
- Сейчас: WS playback_command = `pass` (ничего не делает).
- Realtime control (seek, pause, skip) — нужен рабочий канал.

**Логика работы (если WS):**
```
WebSocket connection:
  Client → Server: { "cmd": "seek", "pos": 30 }
  Server → Client: { "status": "ok", "time_pos": 30 }

  Client → Server: { "cmd": "pause" }
  Server → Client: { "status": "ok", "paused": true }
```

**Логика работы (если REST):**
```
POST /api/playback/seek { "pos": 30 }
POST /api/playback/pause
POST /api/playback/resume
POST /api/playback/skip { "direction": "next" }
```

**Рекомендация:** REST проще, не требует persistent connection. WS — для realtime status push.

**Результат:**
- Remote control: play, pause, seek, skip
- Облако может управлять плеером в реальном времени

---

## Фаза D — Ops (Эксплуатация)
**Цель:** Плеер обновляется сам, работает годами без ручного вмешательства.

---

### D1. dsign-update (Self-Update)
**Что меняем:**
- Новый скрипт `dsign-update`:
  - `check` — сравнить local version с remote (GitHub releases)
  - `download` — скачать новую версию
  - `apply` — backup current, apply update, restart services
  - `rollback` — вернуться к предыдущей версии
- systemd timer: проверять обновления раз в день

**Зачем:**
- Сейчас: обновление = `git pull` вручную + перезагрузка. Не масштабируется.
- 100 плееров = 100 ручных обновлений. Невозможно.

**Логика работы:**
```
Ежедневно в 03:00:
  1. Проверить GitHub releases (или свой сервер)
  2. Если новая версия:
     a. Скачать .tar.gz
     b. Backup /home/dsign/dsign → /home/dsign/dsign.backup
     c. Распаковать, pip install -r requirements.txt
     d. Проверить: `python -m pytest` (smoke test)
     e. Если pass: restart services
     f. Если fail: rollback, alert
  3. Лог: "updated to v1.2.3" или "rollback to v1.2.2"
```

**Результат:**
- Fleet обновляется автоматически
- Rollback при fail
- Zero manual ops

**Acceptance:**
- Update: < 5 мин downtime
- Rollback: < 2 мин
- Fail update → не ломает систему

---

### D2. Local Schedule (SQLite Rules)
**Что меняем:**
- Новая таблица `schedule_rules`:
  - `days` — mon, tue, wed... или "weekdays", "weekends"
  - `time_start`, `time_end` — HH:MM
  - `playlist_id` — что играть
  - `priority` — override rules
  - `active` — on/off
- Background thread: sleep until next rule, apply.

**Зачем:**
- Автономность: плеер сам знает что играть в 09:00, не ждёт облако.
- Offline: если связь пропала — расписание продолжает работать.

**Логика работы:**
```
Правила (загружены с сервера, хранятся в SQLite):
  - weekdays 09:00-12:00 → playlist "Morning"
  - weekdays 12:00-15:00 → playlist "Afternoon"
  - weekends 10:00-14:00 → playlist "Weekend"
  - 2026-12-31 20:00-23:59 → playlist "NY Eve" (date override)

Плеер каждую минуту (или sleep until next event):
  1. now = текущее время
  2. active_rule = правило, где now между start и end
  3. Если active_rule != current_rule:
     - stop current
     - start active_rule.playlist_id
  4. Если нет active_rule → idle logo

При обрыве связи: правила уже в SQLite → работают автономно.
```

**Результат:**
- Плеер автономен
- Расписание работает без облака
- Облако = management, не runtime dependency

**Acceptance:**
- Правило "09:00 playlist A" — стартует в 09:00 ± 30 сек
- Offline 24ч — расписание продолжает работать

---

## Итоговая таблица: 4 фазы

| Фаза | Задачи | Срок | Результат |
|------|--------|------|-----------|
| **A** | A1-A5: Zero-gap playback, safe loadfile, tech debt | 2 недели | Плеер не показывает чёрный экран |
| **C** | C1-C3: ContentCache, audio-only, nested playlists | 1-2 недели | Offline resilience, больше форматов |
| **B** | B1-B5: Status, health, override, token, control | 1-2 недели | Полный remote control |
| **D** | D1-D2: Self-update, local schedule | 1-2 недели | Автономность, zero ops |
| **Итого** | 12 задач | **6-8 недель** | **Референсный плеер** |

---

## Будет ли это коммерчески успешно?

### Что даёт эти 4 фазы

| Фича | Коммерческая ценность |
|------|----------------------|
| Zero-gap playback | **Критично.** Клиенты платят за "экран, который работает". Чёрный кадр = брак. |
| Safe loadfile | **Критично.** Плеер не умирает на битом файле = меньше service calls. |
| ContentCache | **Важно.** Offline playback = работа в метро, ТЦ с плохим интернетом. |
| Audio-only | **Ниша.** Фоновая музыка + logo = новый сегмент (кафе, магазины). |
| Override API | **Важно.** Emergency alerts, ad insertion = доп. монетизация. |
| Health API | **Важно.** Monitoring = proactive support, SLA. |
| Self-update | **Критично.** Fleet из 1000 плееров = impossible без OTA. |
| Local schedule | **Важно.** Автономность = надёжность, меньше зависимости от облака. |

### Чего НЕ хватает для коммерческого успеха

| Что нужно | Почему | Когда |
|-----------|--------|-------|
| **Fleet dashboard** | Управление 1000 плееров = must have | Фаза 2 (после референсного плеера) |
| **Cloud** | SaaS = recurring revenue, не one-time hardware | Фаза 2 |
| **Analytics** | Клиенты хотят знать: сколько показали, engagement | Фаза 2 |
| **Support/SLA** | Enterprise = 99.9% uptime guarantee | Фаза 3 |
| **White-label** | Resellers, интеграторы | Фаза 3 |
| **Hardware partnerships** | All-in-one displays (Samsung, LG) | Фаза 4 |

### Вердикт

**4 фазы делают dsign:**
- ✅ **Технически референсным** (лучший playback engine на рынке)
- ✅ **Готовым к интеграции** (API, health, control)
- ✅ **Эксплуатируемым** (OTA, autonomous)
- ⚠️ **Не коммерчески успешным сам по себе** (нет продажи, billing, support)

**Коммерческий успех = 4 фазы + Fleet Dashboard (Фаза 2)**

4 фазы — это **foundation**. Без них dashboard будет управлять глючным плеером. С ними — управлять надёжным продуктом, который продаётся.

**Правильная стратегия:**
1. Сделать 4 фазы (референсный плеер)
2. Показать интеграторам/клиентам (pilot)
3. Собрать feedback
4. Строить Fleet Dashboard на этом foundation

**Риск:** Если сразу делать dashboard без фикса playback — получится "красивый UI для глючного плеера" = провал.
