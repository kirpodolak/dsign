# DSign — переменные окружения

**Версия:** 2026-07-10  
**Статус:** справочник для Pi / fleet (backlog **P-DOC**).

Код читает переменные через `os.getenv()` в рантайме. Значения по умолчанию — из кода на ветке `main`; при расхождении с устройством смотрите установленный файл или unit systemd.

---

## Где задавать

| Место | Назначение |
|-------|------------|
| `/etc/dsign/api.env` | `DSIGN_API_TOKEN` (Bearer для API / fleet). Шаблон: `etc/dsign/api.env.example`. Runbook: [D2_OPS_FLEET.md](./D2_OPS_FLEET.md) |
| `/var/lib/dsign/config/wayland.env` | Wayland / DRM / MPV display. Шаблон: `etc/dsign/wayland.env.example` |
| `/var/lib/dsign/config/network-assistant.env` | Wi‑Fi assistant на экране (пишет Settings API) |
| `digital-signage.service` / drop-in | Переменные Flask-приложения (`EnvironmentFile=`) |
| `dsign-mpv.service` | MPV-specific overrides |
| Export в shell | Только для отладки; после reboot не сохраняется |

**Приоритет:** `Environment=` в unit systemd **перекрывает** дубликаты в `wayland.env` (см. комментарий в `wayland.env.example`).

---

## Ядро приложения

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_PROJECT_ROOT` | — | Корень проекта ( `server.py` ) |
| `UPLOAD_FOLDER` | `/var/lib/dsign/media` | Медиа и upload root |
| `DSIGN_LOG_DIR` | `/var/log/dsign` | Логи приложения |
| `DSIGN_LOG_LEVEL` | из кода logger | `DEBUG` / `INFO` / `WARNING` / … |
| `SECRET_KEY` | *(dev default в config)* | Flask session secret — **обязательно сменить в production** |
| `SESSION_COOKIE_SECURE` | `false` | `true` при HTTPS |
| `FLASK_ENV` | `production` | `development` → DEBUG |
| `EXTRA_CORS_ORIGINS` | — | Доп. CORS origins через запятую |

## Socket.IO

| Переменная | Default | Описание |
|------------|---------|----------|
| `SOCKETIO_ASYNC_MODE` | `threading` | Режим async Socket.IO |
| `DSIGN_SOCKETIO_PING_INTERVAL` | `25` | Ping interval (сек) |
| `DSIGN_SOCKETIO_PING_TIMEOUT` | `60` | Ping timeout (сек) |
| `DSIGN_SOCKETIO_ENGINEIO_DEBUG` | `false` | Engine.IO debug |

## Auth & API

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_API_TOKEN` | — | Bearer token для `/api/*` (schedule, health, play/stop). Файл: `/etc/dsign/api.env` |

Rate limits (**H-RL**) заданы константами в `dsign/services/api_rate_limit.py` (не env): play 5/min, stop 10/min, screenshot 6/min, service restart 3/min, reboot 1/h, global 100/min.

---

## Upload & transcode

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_UPLOAD_DISK_RESERVE_BYTES` | `52428800` (50 MiB) | Запас диска до save (**H-UPL**) |
| `DSIGN_UPLOAD_STREAM_THRESHOLD_BYTES` | `104857600` (100 MiB) | Порог chunked streaming upload |
| `DSIGN_UPLOAD_CHUNK_BYTES` | `1048576` (1 MiB) | Размер чанка при streaming |
| `DSIGN_AUTO_TRANSCODE_VIDEOS` | `true` | Фоновый ffmpeg transcode после upload |
| `DSIGN_TRANSCODE_TARGET_RESOLUTION` | `1920x1080` | Целевое разрешение transcode |
| `DSIGN_TRANSCODE_TARGET_FPS` | `25` | Целевой FPS transcode |

`MAX_UPLOAD_BYTES` / `MAX_CONTENT_LENGTH` — в `dsign/config/config.py` (1 GiB), не отдельная env.

---

## Content cache (C1)

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_CONTENT_CACHE_ENABLED` | `1` | Включить disk cache внешних URL |
| `DSIGN_CONTENT_CACHE_DIR` | `{upload}/cache` | Каталог кэша (если пусто — рядом с media) |
| `DSIGN_CONTENT_CACHE_PREFETCH` | `1` | Prefetch следующего item |
| `DSIGN_CONTENT_CACHE_PREFETCH_WORKERS` | `1` | Потоки prefetch pool (**H-PREF**, max 4) |
| `DSIGN_CONTENT_CACHE_PLAY_WHEN_READY` | `1` | Играть с диска, если файл готов |
| `DSIGN_CONTENT_CACHE_MAX_GB` | `8` | Лимит кэша, LRU eviction |
| `DSIGN_CONTENT_CACHE_DOWNLOAD_SEC` | `7200` | Таймаут yt-dlp download (сек) |
| `DSIGN_CONTENT_CACHE_DOWNLOAD_ATTEMPTS` | `3` | Попытки download (**H-CACHE**, max 5) |
| `DSIGN_CONTENT_CACHE_RETRY_BASE_SEC` | `2` | База backoff между попытками |
| `DSIGN_CONTENT_CACHE_RETRY_MAX_SEC` | `120` | Потолок backoff |
| `DSIGN_YTDLP_PATH` | `/usr/bin/yt-dlp` | Путь к yt-dlp |

---

## Graceful shutdown & recovery

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_SHUTDOWN_JOIN_SEC` | `8` | Join playback thread при SIGTERM/SIGINT (**H-SD**, 1–60) |
| `DSIGN_RECOVERY_QUEUE_MAX` | `8` | Размер очереди recovery (**H-RQ**, 1–32) |
| `DSIGN_MEDIA_BACKOFF_TTL_SEC` | `3600` | TTL `_media_backoff` (**H-MEM**, 60–86400) |

---

## MPV — display & audio

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_DISPLAY_BACKEND` | auto | `wayland` / `x11` — backend вывода |
| `DSIGN_MPV_UNIT` | `dsign-mpv.service` | systemd unit MPV |
| `DSIGN_MPV_VO` | `gpu` | MPV video output |
| `DSIGN_MPV_AO` | `alsa` | MPV audio output |
| `DSIGN_MPV_AUDIO_DEVICE` | `auto` | ALSA device для MPV |
| `DSIGN_ALSA_CARD` | — | Override ALSA card (API `amixer`) |
| `DSIGN_ALSA_CTL` | `PCM` | ALSA control name |
| `DSIGN_ALSA_HDMI_DEV` | — | HDMI device из settings / env |
| `DSIGN_USE_PIPEWIRE_AUDIO` | `1` | PipeWire (`wpctl`) для master volume |
| `DSIGN_PREFER_MPV_VOLUME` | `1` | Предпочитать MPV volume property |
| `DSIGN_MPV_AUDIO_POLL_CACHE_SEC` | — | TTL кэша poll audio status API |
| `DSIGN_AUDIO_WAYLAND_VO_NULL` | `0` | Idle logo: `vo=null` на Wayland |
| `DSIGN_PLAYLIST_TRANSITION` | `logo` | Переход между playlist: `logo` / … |
| `DSIGN_TRANSITION_BLACK_SRC` | — | Чёрный кадр для transition |
| `DSIGN_MPV_TRANSITION_TIMEOUT_SEC` | `2.5` | Таймаут transition load |
| `DSIGN_IMAGE_TIMER_MODE` | `from_load` | Таймер слайда: `from_load` / `from_ready` |

## MPV — IPC, restart, watchdog

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_MPV_RESTART_COALESCE_SEC` | `8` | База coalesce systemd restart (**H-COAL**) |
| `DSIGN_MPV_RESTART_COALESCE_MAX_SEC` | `60` | Потолок coalesce при IPC streak |
| `DSIGN_MPV_RESTART_DURING_PLAYBACK` | `0` | Разрешить restart во время playlist |
| `DSIGN_MPV_PLAYBACK_HUNG_RESTART_AFTER` | `8` | IPC failures до hung restart |
| `DSIGN_MPV_SOCKET_WATCH` | `1` | Фоновый watch IPC socket recreate |
| `DSIGN_MPV_SOCKET_WATCH_SEC` | `3` | Интервал socket watch |
| `DSIGN_MPV_IPC_WATCHDOG` | `1` | IPC watchdog thread |
| `DSIGN_MPV_IPC_WATCHDOG_SEC` | `15` | Период watchdog |
| `DSIGN_MPV_IPC_WATCHDOG_FAIL_LIMIT` | `4` | Failures до action |
| `DSIGN_MPV_IPC_WATCHDOG_PROBE_SEC` | `4` | Probe timeout |
| `DSIGN_MPV_IPC_LOCK_TIMEOUT_SEC` | `6` | IPC lock (idle) |
| `DSIGN_MPV_IPC_LOCK_TIMEOUT_PLAYBACK_SEC` | `0.35` | IPC lock во время playback |
| `DSIGN_MPV_IPC_LOCK_TIMEOUT_LONG_SEC` | `45` | IPC lock long ops |
| `DSIGN_MPV_IPC_DEBUG` | — | Verbose IPC logging |
| `DSIGN_MPV_IPC_SLOW_MS` | `250` | Log IPC slower than (ms) |
| `DSIGN_MPV_SOCKET_RECOVER_SEC` | `12` | Socket recover wait |
| `DSIGN_MPV_SOCKET_WAIT_ATTEMPTS` | `20` | Startup socket wait attempts |
| `DSIGN_MPV_SOCKET_WAIT_INTERVAL_SEC` | `1` | Interval between attempts |
| `DSIGN_MPV_SOCKET_WAIT_RESTART_AFTER` | `8` | Attempt № для restart MPV |
| `DSIGN_MPV_STARTUP_RESTART_MPV` | `0` | Restart MPV на startup если нет socket |
| `DSIGN_MPV_RECOVER_BIN` | `/usr/local/bin/dsign-mpv-recover` | Helper binary |
| `DSIGN_MPV_RECOVER_PLAY_DELAY_SEC` | `2.5` | Delay перед play после recover |
| `DSIGN_MPV_POST_INIT_SETTLE_SEC` | `3` | Settle после MPV init |
| `DSIGN_MPV_EOF_USE_EVENTS` | `1` | EOF через MPV events |
| `DSIGN_MPV_LOG_FILE` | `/var/lib/dsign/mpv/mpv.log` | Путь лога mpv |

## MPV — playlist / network / EOF

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_MPV_YTDL_OPEN_SEC` | dynamic | Таймаут открытия ytdl stream |
| `DSIGN_MPV_YTDL_OPEN_SEC_AFTER_RECOVER` | `60` | ytdl open после MPV recover |
| `DSIGN_MPV_YTDL_LOADFILE_IPC_SEC` | `12` | loadfile IPC timeout |
| `DSIGN_MPV_DEMUXER_WAIT_SEC` | `45` | demuxer-read-timeout |
| `DSIGN_MPV_NETBUF` | off | Включить demuxer cache (`1`/`true`) |
| `DSIGN_MPV_NETBUF_SECS` | `12` | cache-secs |
| `DSIGN_MPV_NETBUF_MAX_MB` | `96` | demuxer-max-bytes |
| `DSIGN_MPV_NETBUF_BACK_MB` | `16` | demuxer-readahead-threshold |
| `DSIGN_MPV_NETBUF_READAHEAD_SECS` | `20` | cache-pause-initial |
| `DSIGN_MPV_STREAM_READY_SECS` | `120` | Ждать готовности stream |
| `DSIGN_MPV_STREAM_READY_GRACE_SEC` | `45` | Grace после ready |
| `DSIGN_MPV_NETWORK_IDLE_GRACE_SEC` | `25` | Network idle до EOF logic |
| `DSIGN_MPV_NETWORK_EOF_POLL_SEC` | `3` | Poll interval near EOF |
| `DSIGN_MPV_NETWORK_EOF_ADVANCE_SEC` | `8` | Advance при network EOF |
| `DSIGN_MPV_NETWORK_NEAR_EOF_STAGNATION_SEC` | `15` | Stagnation near EOF |
| `DSIGN_MPV_PLAYBACK_STAGNATION_SEC` | `90` | Local stagnation EOF |
| `DSIGN_MPV_EOF_IPC_STALL_POLLS` | context | Stall polls (local/network) |
| `DSIGN_MPV_EOF_IPC_DEAD_SEC` | `45` | IPC dead при EOF wait |
| `DSIGN_MPV_EOF_SNAPSHOT_TIMEOUT_SEC` | — | Snapshot timeout EOF path |
| `DSIGN_MPV_LAVF_TIMEOUT_SEC` | `15` | network-timeout lavf |
| `DSIGN_MPV_STALL_ADVANCE_AFTER` | `2` | Stall count до advance |
| `DSIGN_MPV_MIDSTREAM_RELOAD_MAX` | `1` | Mid-stream reload limit |
| `DSIGN_MPV_MIDSTREAM_IPC_ADVANCE` | `1` | Advance при midstream IPC fail |
| `DSIGN_MPV_PROACTIVE_REFRESH_INTERVAL_MIN` | — | Proactive URL refresh interval |
| `DSIGN_MPV_PROACTIVE_REFRESH_MIN` | alias | Старое имя (alias) |
| `DSIGN_MPV_PROACTIVE_REFRESH_MIN_DURATION_SEC` | `0` | Min duration для refresh |
| `DSIGN_PLAYLIST_NET_OPEN_FAIL_ABORT` | `3` | Abort после N open failures |
| `DSIGN_PLAYLIST_NET_OPEN_FAIL_COOLDOWN_SEC` | `90` | Cooldown после net fail |
| `DSIGN_PLAYLIST_ALL_NETWORK_FAIL_COOLDOWN_SEC` | `300` | Cooldown all-network fail |
| `DSIGN_PLAYBACK_STATUS_IPC_TTL_SEC` | `2` | TTL кэша playback status IPC |

---

## Boot & network assistant

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_BOOT_WIFI_PROMPT_WAIT_SEC` | `90` | Ждать Wi‑Fi prompt при boot |
| `DSIGN_BOOT_PLAYLIST_DELAY_SEC` | `12` | Задержка autostart playlist |
| `DSIGN_BOOT_IP_POST_MARKER_SEC` | `3` | Grace после startup IP marker |
| `DSIGN_NETWORK_ASSISTANT_INTERACTIVE` | `0` | Interactive network assistant |
| `DSIGN_NETWORK_ASSISTANT_FORCE_PROMPT` | `0` | Force prompt |
| `DSIGN_NETWORK_ASSISTANT_DEBUG` | `0` | Debug network assistant |
| `DSIGN_NETWORK_WIFI_ON_DISPLAY` | `0` | Wi‑Fi UI на экране |
| `DSIGN_NETWORK_STATUS_DISPLAY_SEC` | `10` | Длительность status overlay |
| `DSIGN_NETWORK_WIFI_PROMPT_SEC` | `0` | Wi‑Fi prompt duration |

---

## Wayland / compositor (не DSIGN_, но на устройстве)

Задаются в `/var/lib/dsign/config/wayland.env`:

| Переменная | Пример | Описание |
|------------|--------|----------|
| `WAYLAND_DISPLAY` | `wayland-0` | Имя Wayland socket |
| `XDG_RUNTIME_DIR` | `/run/user/1000` | Runtime dir пользователя dsign |
| `WLR_DRM_DEVICES` | `/dev/dri/card0` | DRM card для labwc |
| `WLR_BACKENDS` | `drm` | wlroots backend |
| `WLR_RENDERER` | `vulkan` | Renderer (vulkan/gles2/pixman) |
| `LIBSEAT_BACKEND` | `seatd` | seat backend |

См. `etc/dsign/wayland.env.example`, `dsign-sync-wayland-drm`.

---

## Прочее

| Переменная | Default | Описание |
|------------|---------|----------|
| `DSIGN_M3U_EXPORT_FALLBACK_DIR` | `/var/lib/dsign/m3u` | Fallback export M3U |
| `DSIGN_UID` | `1000` | UID для путей runtime |

---

## Связанные документы

- [dsign_backlog.md](./dsign_backlog.md) — ID задач (H-*, P-DOC)
- [dsign_improvement_checklist.md](./dsign_improvement_checklist.md) — hardening детали
- [dsign_4phase_checklist.md](./dsign_4phase_checklist.md) — Content cache C1
- `etc/dsign/api.env.example`, `etc/dsign/wayland.env.example`

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-07-10 | P-DOC: первый справочник env vars |
