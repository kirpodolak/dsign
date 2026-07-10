# D2-OPS — Fleet Bearer token (`DSIGN_API_TOKEN`)

**Версия:** 2026-07-10  
**Backlog:** `D2-OPS`  
**Статус кода:** schedule API принимает session **или** Bearer с D2.5 (`@api_session_or_token_required`).

Этот документ — **ops-runbook** для настройки fleet-мониторинга и remote schedule API. Выполняется **на каждом плеере** (или через ваш Ansible/SSH).

См. также: [ENVIRONMENT.md](./ENVIRONMENT.md) · [dsign_schedule_spec.md](./dsign_schedule_spec.md) · [dsign_4phase_checklist.md](./dsign_4phase_checklist.md) §B4/D2.5

---

## 1. Установка токена на плеере

```bash
# Создать /etc/dsign/api.env (идемпотентно при reinstall)
sudo dsign-api-token install

# Проверить файл и что unit подхватил env
sudo dsign-api-token status

# Показать токен (root only) — сохранить в vault / monitoring
sudo dsign-api-token show
```

Файл: `/etc/dsign/api.env` (mode `600`, owner `dsign`).  
Шаблон в репо: `etc/dsign/api.env.example`.

После `install` / `rotate` скрипт перезапускает `digital-signage.service`, если он active.

---

## 2. Быстрая проверка Bearer (health)

```bash
TOKEN=$(sudo dsign-api-token show | cut -d= -f2-)
BASE="http://127.0.0.1:5000"

curl -sf -H "Authorization: Bearer $TOKEN" "$BASE/api/health" | jq .
# ожидание: success=true, healthy=...
```

С удалённой машины (замените IP):

```bash
curl -sf -H "Authorization: Bearer $TOKEN" "http://<PLAYER_IP>:5000/api/health" | jq .
```

---

## 3. Schedule API — curl smoke (D2.5)

Bearer **не требует** `X-CSRFToken` на mutating schedule endpoints.

```bash
TOKEN=$(sudo dsign-api-token show | cut -d= -f2-)
H="Authorization: Bearer $TOKEN"
BASE="http://127.0.0.1:5000"
```

| Шаг | Команда | Ожидание |
|-----|---------|----------|
| List rules | `curl -sf -H "$H" "$BASE/api/schedule/rules" \| jq .` | `success: true`, `rules: [...]` |
| Month view | `curl -sf -H "$H" "$BASE/api/schedule/month?date=$(date +%Y-%m-01)" \| jq .` | `success: true`, `instances` |
| Week view | `curl -sf -H "$H" "$BASE/api/schedule/week?date=$(date +%Y-%m-%d)" \| jq .` | `success: true` |
| Now | `curl -sf -H "$H" "$BASE/api/schedule/now" \| jq .` | `success: true` |
| Playback status | `curl -sf -H "$H" "$BASE/api/playback/status" \| jq .` | snapshot playback |

### Создание правила (пример, нужен существующий `playlist_id`)

```bash
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  "$BASE/api/schedule/rules" \
  -d '{
    "playlist_id": 1,
    "days_of_week": 127,
    "start_time": "09:00",
    "end_time": "18:00",
    "repeat_type": "weekly",
    "priority": 5
  }' | jq .
```

### Exception без CSRF

```bash
RULE_ID=1   # из предыдущего ответа
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  "$BASE/api/schedule/exceptions" \
  -d "{\"rule_id\": $RULE_ID, \"date\": \"$(date +%Y-%m-%d)\"}" | jq .
```

### Return to schedule (playback)

```bash
curl -sf -X POST -H "$H" "$BASE/api/playback/return-to-schedule" | jq .
```

### Негативный тест

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer wrong-token" \
  "$BASE/api/schedule/rules"
# ожидание: 401
```

---

## 4. Fleet checklist (ops)

Отметьте на **каждом** устройстве после deploy / OTA:

- [ ] `sudo dsign-api-token status` → token set, `DSIGN_API_TOKEN loaded` в service env
- [ ] `curl` health с Bearer → `200`
- [ ] `GET /api/schedule/rules` с Bearer → `200`
- [ ] `GET /api/schedule/month` с Bearer → `200`
- [ ] Токен сохранён в secrets manager (не в git, не в скриншотах)
- [ ] Monitoring использует Bearer (не session cookie)
- [ ] После `rotate` обновлены все интеграции

**Fleet-wide rollout:** когда все плееры прошли checklist — закрыть `D2-OPS` в [dsign_backlog.md](./dsign_backlog.md).

---

## 5. Rotate / incident

```bash
sudo dsign-api-token rotate -y
# обновить токен во всех monitoring scripts / central hub
```

Старый токен перестаёт работать сразу после `rotate`.

---

## 6. Автотесты (регрессия в CI)

Покрытие Bearer + schedule без CSRF: `dsign/tests/test_api_smoke.py` (backlog **T-API**).

На Pi после sync ветки:

```bash
cd /home/dsign/dsign && python3 -m pytest tests/test_api_smoke.py -q
```

---

## 7. Что остаётся session-only

Галерея, upload, settings UI, Wi‑Fi connect — **session + CSRF**.  
Bearer предназначен для: health, playback control, **schedule API** (D2.5).

Полная таблица endpoints: [dsign_4phase_checklist.md](./dsign_4phase_checklist.md) §B4.
