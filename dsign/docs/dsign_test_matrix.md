[dsign_test_matrix.md](https://github.com/user-attachments/files/29330714/dsign_test_matrix.md)

# Acceptance Criteria — Тест-матрица playback dsign
# Версия: 1.0 | Приоритет: A (playback reliability)
# Цель: "Ноль чёрных кадров", zero-gap, corrupt resilience

---

## Общие правила для всех тестов

### Окружение
- **Device**: Raspberry Pi 4/5 (тестовый стенд) или целевое железо
- **OS**: Raspberry Pi OS Bookworm (Debian 12)
- **Display**: 1920x1080@60Hz HDMI
- **Backend**: Wayland (labwc) — единственный поддерживаемый
- **MPV**: >= 0.38 (проверить `mpv --version`)
- **Clean state**: `sudo systemctl restart dsign-compositor dsign-logo dsign-mpv-wayland digital-signage`

### Методика измерения
- **Визуальная оценка**: Глаз эксперта (playback operator)
- **Видеозапись**: 240fps camera (iPhone Slow-Mo или аналог) для объективной оценки gap
- **MPV logs**: `journalctl -u dsign-mpv -f` — смотреть `loadfile`, `eof`, `idle`
- **Frame timing**: `DSIGN_MPV_IPC_DEBUG=1` — логировать каждый IPC call

### Критерии оценки

| Grade | Описание | Допустимо для |
|-------|----------|---------------|
| **A** | Zero perceptible gap (< 50ms) | Референсный плеер |
| **B** | Micro-flash (50-150ms) | Приемлемо для beta |
| **C** | Visible black frame (150-500ms) | Не приемлемо для релиза |
| **F** | Freeze, hang, or crash | Блокер, must fix |

---

## Тест 1: Boot → Logo → Content

### ID: BOOT-001
### Цель: Плеер стартует, показывает логотип, начинает playback без чёрного экрана

#### Preconditions
- [ ] Устройство выключено
- [ ] В БД `PlaybackStatus` = `playing`, `playlist_id` = 1
- [ ] Плейлист 1 = локальное видео (test_video_1080p_30fps.mp4)
- [ ] Логотип idle_logo.jpg в `/var/lib/dsign/media/`

#### Steps
1. Включить устройство (подача питания)
2. Засечь время от подачи питания до появления логотипа
3. Засечь время от логотипа до начала видео
4. Наблюдать: есть ли чёрный экран между логотипом и видео?

#### Expected Results
| Метрика | Минимум | Цель | Максимум |
|---------|---------|------|----------|
| Logo visible after boot | 5 sec | 3 sec | 10 sec |
| Video starts after logo | 10 sec | 5 sec | 15 sec |
| Black gap logo→video | 0 ms | 0 ms | 50 ms (Grade A) |
| Total boot→playback | 15 sec | 8 sec | 20 sec |

#### Acceptance Criteria
- [ ] **PASS**: Логотип виден ≤ 10 сек от питания
- [ ] **PASS**: Видео стартует ≤ 15 сек от питания
- [ ] **PASS**: Нет видимого чёрного экрана между logo и video (Grade A или B)
- [ ] **FAIL**: Чёрный экран > 150ms (Grade C) → блокер
- [ ] **FAIL**: Видео не стартует автоматически → блокер

#### Logs to capture
```bash
sudo journalctl -u dsign-compositor -u dsign-logo -u dsign-mpv-wayland -u digital-signage --since "1 minute ago"
```

---

## Тест 2: Video → Video (Local Files)

### ID: VID-VID-001
### Цель: Переход между двумя локальными видео — zero gap

#### Preconditions
- [ ] Два локальных видео: `video_a_1080p_30fps_10sec.mp4`, `video_b_1080p_30fps_10sec.mp4`
- [ ] Одинаковые параметры: resolution, fps, codec (h264)
- [ ] Плейлист: [A, B], loop

#### Steps
1. Стартовать плейлист
2. Дождаться окончания video A (или перемотать на 2 сек до конца)
3. Наблюдать переход A → B
4. Повторить 10 циклов

#### Expected Results
| Метрика | Цель | Максимум |
|---------|------|----------|
| Gap A → B | 0 ms | 50 ms |
| Visual artifact | none | 1 frame flash |
| Audio gap | 0 ms | 20 ms |
| Consistency (10 cycles) | 10/10 Grade A | 8/10 Grade A |

#### Acceptance Criteria
- [ ] **PASS**: 10/10 переходов Grade A (zero gap)
- [ ] **PASS**: Нет audio click/pop при переходе
- [ ] **FAIL**: Любой переход Grade C (black frame) → блокер
- [ ] **FAIL**: Audio dropout > 50ms → блокер

#### Variations
- **VID-VID-002**: Разные fps (30 → 60) — acceptable micro-adjustment
- **VID-VID-003**: Разные resolution (1080p → 720p) — test scaling
- **VID-VID-004**: Разные codecs (h264 → h265) — test hwdec switch

---

## Тест 3: Video → Image → Video

### ID: VID-IMG-001
### Цель: Переход видео → картинка (5 сек) → видео — без мерцания

#### Preconditions
- [ ] Видео A: 10 сек
- [ ] Картинка: `image_1920x1080.jpg`, duration = 5 сек
- [ ] Видео B: 10 сек
- [ ] Плейлист: [A, image, B]

#### Steps
1. Стартовать плейлист
2. Наблюдать: A → image (конец видео)
3. Наблюдать: image → B (после 5 сек)

#### Expected Results
| Переход | Цель | Максимум |
|---------|------|----------|
| A → image | Cut (no black) | 50 ms gap |
| image → B | Cut (no black) | 50 ms gap |
| Image display | Ровно 5 сек ± 0.5 сек | ± 1 сек |

#### Acceptance Criteria
- [ ] **PASS**: Оба перехода Grade A или B
- [ ] **PASS**: Картинка показывается ровно 5 сек (timer accuracy)
- [ ] **FAIL**: Чёрный экран при любом переходе > 150ms → блокер
- [ ] **FAIL**: Картинка < 4 сек или > 7 сек → баг

#### Variations
- **VID-IMG-002**: Image → Image (slideshow) — test timer drift
- **VID-IMG-003**: Image → Image (100 items) — test memory, no leak

---

## Тест 4: Image → Image (Slideshow)

### ID: IMG-IMG-001
### Цель: Слайдшоу картинок — без мерцания, точный таймер

#### Preconditions
- [ ] 5 картинок, каждая 3 сек
- [ ] Плейлист: [img1, img2, img3, img4, img5], loop

#### Steps
1. Стартовать плейлист
2. Засечь время каждого слайда (stopwatch)
3. Повторить 3 цикла (15 слайдов)

#### Expected Results
| Метрика | Цель | Максимум |
|---------|------|----------|
| Per-image duration | 3.0 сек ± 0.1 | 3.0 сек ± 0.5 |
| Transition gap | 0 ms | 50 ms |
| Timer drift (3 cycles) | < 0.5 сек | < 2 сек |
| Memory usage | stable | < +50MB за 15 min |

#### Acceptance Criteria
- [ ] **PASS**: Все 15 слайдов Grade A/B
- [ ] **PASS**: Timer drift < 2 сек за 3 цикла
- [ ] **PASS**: Memory не растёт (no leak)
- [ ] **FAIL**: Gap > 150ms между картинками → блокер
- [ ] **FAIL**: Timer drift > 5 сек → баг

---

## Тест 5: Single-Item Loop

### ID: LOOP-001
### Цель: Один файл loop'ится бесшовно, без reload

#### Preconditions
- [ ] Одно видео: `loop_test_1080p_30fps_5sec.mp4`
- [ ] Плейлист: [video], loop enabled

#### Steps
1. Стартовать плейлист
2. Дождаться 5-го цикла (25 сек)
3. Наблюдать: есть ли gap при loop?
4. Проверить MPV logs: сколько `loadfile` вызовов?

#### Expected Results
| Метрика | Цель (с `loop-file=inf`) | Текущее (reload) |
|---------|------------------------|------------------|
| Gap at loop point | 0 ms | 50-500 ms |
| `loadfile` calls | 1 (только при старте) | 1 per loop |
| CPU usage | stable | spike при reload |

#### Acceptance Criteria
- [ ] **PASS**: Zero gap при loop (Grade A)
- [ ] **PASS**: Только 1 `loadfile` в логах
- [ ] **FAIL**: Видимый gap при loop → блокер
- [ ] **FAIL**: `loadfile` на каждом цикле → баг (не используем `loop-file=inf`)

#### Variations
- **LOOP-002**: Single image loop — test timer stability
- **LOOP-003**: 1-hour video loop — test long-term stability

---

## Тест 6: Corrupt / Missing File

### ID: CORRUPT-001
### Цель: Плеер не умирает на битом файле, пропускает и продолжает

#### Preconditions
- [ ] Плейлист: [valid_video, corrupt_video, valid_video2]
- [ ] `corrupt_video` = valid file с повреждёнными первыми 100KB (truncate)
- [ ] `missing_video` = файл указан в БД, но удалён с диска

#### Steps
1. Стартовать плейлист
2. Наблюдать: что происходит на corrupt_video?
3. Проверить: продолжает ли playback valid_video2?
4. Проверить логи: есть ли error + skip?

#### Expected Results
| Сценарий | Цель | Максимум |
|----------|------|----------|
| Corrupt file | Skip, log error, continue next | Hang < 5 сек |
| Missing file | Skip, log error, continue next | Hang < 2 сек |
| Recovery time | < 2 сек | < 5 сек |
| Logo visible | Yes (during skip) | No (black screen) |

#### Acceptance Criteria
- [ ] **PASS**: Corrupt file skipped, playback continues
- [ ] **PASS**: Missing file skipped, playback continues
- [ ] **PASS**: Error logged с именем файла и причиной
- [ ] **PASS**: Logo visible во время recovery (не чёрный экран)
- [ ] **FAIL**: Плеер hangs > 10 сек → блокер
- [ ] **FAIL**: Плеер crash/restart → блокер
- [ ] **FAIL**: Плеер останавливается (не переходит к next) → блокер

#### Variations
- **CORRUPT-002**: Corrupt image (truncated JPG)
- **CORRUPT-003**: Zero-byte file
- **CORRUPT-004**: Wrong extension (mp4 renamed to jpg)
- **CORRUPT-005**: Permission denied (chmod 000)

---

## Тест 7: Network Stall (Rutube/VK)

### ID: NET-001
### Цель: Сетевой сталл не приводит к чёрному экрану

#### Preconditions
- [ ] Плейлист: [rutube_stream, local_video]
- [ ] Стабильное интернет-соединение

#### Steps
1. Стартовать плейлист
2. Во время rutube_stream: отключить интернет (pull cable / iptables DROP)
3. Наблюдать: что происходит?
4. Включить интернет через 30 сек
5. Наблюдать: recovery?

#### Expected Results
| Фаза | Цель | Максимум |
|------|------|----------|
| Internet lost | Logo visible, retry in background | Black screen < 2 сек |
| Stall duration | Retry каждые 10 сек | Hang > 60 сек |
| Internet restored | Auto-resume stream | Manual restart needed |
| Fallback | Switch to local_video if configured | Stuck on logo forever |

#### Acceptance Criteria
- [ ] **PASS**: Logo visible при stall (не чёрный экран)
- [ ] **PASS**: Автоматический retry каждые N сек
- [ ] **PASS**: Автоматический resume при восстановлении
- [ ] **FAIL**: Чёрный экран > 5 сек → блокер
- [ ] **FAIL**: Плеер crash → блокер
- [ ] **FAIL**: Требуется manual restart → баг

#### Variations
- **NET-002**: Slow network (throttle to 100kbps)
- **NET-003**: Intermittent drops (10% packet loss)
- **NET-004**: DNS failure (block 53/udp)
- **NET-005**: TLS failure (wrong system time)

---

## Тест 8: 24/7 Uptime

### ID: UPTIME-001
### Цель: Плеер работает 48 часов без restart, memory stable

#### Preconditions
- [ ] Плейлист: mixed content (video + image), loop
- [ ] Monitoring: `htop`, `df`, `vcgencmd measure_temp` logging

#### Steps
1. Стартовать playback
2. Оставить на 48 часов
3. Каждые 6 часов: проверить status, screenshot, memory
4. На 48 час: полный health check

#### Expected Results
| Метрика | Цель | Максимум |
|---------|------|----------|
| Uptime | 48 ч | 24 ч (minimum) |
| Memory growth | < 10 MB | < 50 MB |
| Temperature | < 70°C | < 80°C |
| Disk usage | Stable | < 100 MB growth (logs) |
| Playback quality | No degradation | 1 hung acceptable |

#### Acceptance Criteria
- [ ] **PASS**: 48 ч без manual restart
- [ ] **PASS**: Memory stable (no leak)
- [ ] **PASS**: Temperature < 80°C
- [ ] **FAIL**: Crash или hung → блокер
- [ ] **FAIL**: Memory leak > 100 MB → баг
- [ ] **FAIL**: Требуется manual restart → баг

---

## Тест 9: MPV Crash / Hung Recovery

### ID: RECOVERY-001
### Цель: При crash MPV — автоматический recovery с resume

#### Preconditions
- [ ] Плейлист: [video_a, video_b], loop
- [ ] `PlaybackStatus` отслеживает текущий item

#### Steps
1. Стартовать playback
2. Во время video_a: `sudo kill -9 $(pgrep mpv)` (simulate crash)
3. Наблюдать: что происходит?
4. Засечь время до recovery
5. Проверить: resume с того же item?

#### Expected Results
| Метрика | Цель | Максимум |
|---------|------|----------|
| Logo visible | ≤ 2 сек | ≤ 5 сек |
| MPV restart | ≤ 10 сек | ≤ 30 сек |
| Resume item | Same (video_a) | Next (video_b) acceptable |
| Total recovery | ≤ 15 сек | ≤ 30 сек |

#### Acceptance Criteria
- [ ] **PASS**: Logo visible ≤ 5 сек
- [ ] **PASS**: MPV restart автоматический
- [ ] **PASS**: Playback resume (same or next item)
- [ ] **FAIL**: Чёрный экран > 10 сек → блокер
- [ ] **FAIL**: Плеер остаётся на logo (не resume) → блокер
- [ ] **FAIL**: Manual restart required → блокер

#### Variations
- **RECOVERY-002**: `kill -STOP` (freeze) — test watchdog timeout
- **RECOVERY-003**: `systemctl restart dsign-mpv-wayland` — test systemd recovery
- **RECOVERY-004**: Corrupt MPV socket — test socket-watch

---

## Тест 10: Emergency Override

### ID: EMERG-001
### Цель: Внешняя команда мгновенно прерывает текущий контент

#### Preconditions
- [ ] Плейлист A играет (loop)
- [ ] Emergency playlist B (1 видео, 10 сек)
- [ ] API: `POST /api/playback/override`

#### Steps
1. Стартовать плейлист A
2. Вызвать override с playlist B
3. Наблюдать: мгновенный switch?
4. Дождаться окончания B (10 сек)
5. Наблюдать: return to A?

#### Expected Results
| Метрика | Цель | Максимум |
|---------|------|----------|
| Override latency | < 1 сек | < 3 сек |
| Transition | Cut (no black) | < 100 ms gap |
| Auto-return | Yes, to A | Manual restart |
| Return gap | < 1 сек | < 3 сек |

#### Acceptance Criteria
- [ ] **PASS**: Override < 3 сек
- [ ] **PASS**: Auto-return после emergency
- [ ] **PASS**: Нет чёрного экрана при override
- [ ] **FAIL**: Override > 5 сек → баг
- [ ] **FAIL**: Нет auto-return → баг
- [ ] **FAIL**: Crash при override → блокер

---

## Тест 11: Hardware Profiles

### ID: HW-001
### Цель: Автоматический выбор профиля под железо

#### Preconditions
- [ ] Тест на Pi 4, Pi 5, x86 (если доступно)
- [ ] Профили: `rpi-v4l2m2m`, `intel-vaapi`, `software`

#### Steps
1. Чистый install на каждое устройство
2. Проверить: `DSIGN_MPV_PROFILE` auto-detected?
3. Проверить: `hwdec` работает? (`mpv` log: `Using hardware decoding`)
4. Проверить: CPU usage при playback (< 30% для hwdec)

#### Expected Results
| Платформа | Профиль | CPU | hwdec |
|-----------|---------|-----|-------|
| Pi 4 | rpi-v4l2m2m | < 30% | v4l2m2m |
| Pi 5 | rpi-v4l2m2m | < 20% | v4l2m2m |
| x86 Intel | intel-vaapi | < 15% | vaapi |
| Generic | software | < 80% | none |

#### Acceptance Criteria
- [ ] **PASS**: Auto-detection работает (не manual config)
- [ ] **PASS**: hwdec активен на supported hardware
- [ ] **PASS**: CPU < 30% при hwdec
- [ ] **FAIL**: Software decode на Pi → баг (перегрев)
- [ ] **FAIL**: Crash с hwdec → fallback to software

---

## Регрессионный чеклист (перед каждым релизом)

- [ ] BOOT-001: Boot → logo → video
- [ ] VID-VID-001: Video → video (10 cycles)
- [ ] VID-IMG-001: Video → image → video
- [ ] IMG-IMG-001: Image slideshow (3 cycles)
- [ ] LOOP-001: Single-item loop (5 cycles)
- [ ] CORRUPT-001: Corrupt + missing file
- [ ] NET-001: Network stall + recovery
- [ ] RECOVERY-001: MPV crash recovery
- [ ] UPTIME-001: 48-hour soak (перед major release)

---

## Инструменты для тестирования

### Автоматизация (цель)
```python
# test_playback.py — pytest suite

class TestPlaybackTransitions:
    def test_video_to_video_zero_gap(self, mpv, camera):
        start_recording(camera)
        play_playlist([video_a, video_b])
        sleep(20)  # 2 cycles
        frames = stop_recording(camera)

        gaps = detect_black_frames(frames)
        assert all(gap < 50 for gap in gaps), f"Gaps: {gaps}"

    def test_single_item_loop_no_reload(self, mpv):
        play_playlist([video_loop])
        sleep(60)  # 12 loops

        logs = get_mpv_logs()
        loadfile_count = logs.count("loadfile")
        assert loadfile_count == 1, f"Loadfile calls: {loadfile_count}"
```

### Ручное тестирование (сейчас)
- iPhone 240fps Slow-Mo для gap detection
- `journalctl -f` для логов
- `htop` для ресурсов
- `vcgencmd measure_temp` для температуры

---

## Критерии готовности к "референсному" статусу

| Milestone | Критерий | Статус |
|-----------|----------|--------|
| **Alpha** | Все P0 тесты PASS (BOOT, VID-VID, VID-IMG, CORRUPT, RECOVERY) | ⬜ |
| **Beta** | + UPTIME-001 24ч, + NET-001, + LOOP-001 | ⬜ |
| **RC** | + UPTIME-001 48ч, + EMERG-001, + HW-001 | ⬜ |
| **Release** | + Регрессионный чеклист automated, + docs | ⬜ |
