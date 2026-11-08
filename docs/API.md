# API: CLI (факт) + HTTP (контракт M2) + отчёты

> §1 — зеркало живого CLI (проверено `revo.py --help` и selftest 12/12, 2026-11-09).
> §2 — контракт M2: **реализован и принят 2026-11-09 (v1.1)** — смоук `tests/smoke_api.sh` PASS=18 FAIL=0,
> живая браузерная приёмка M3 против этого сервера (скриншоты `demo/ui/`).
> §3 — схемы машинных отчётов. Связка: [SPEC.md](../SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. CLI-контракт `revo.py` (живое ядро)

Общий выход: валидация упала → exit 2; selftest не весь зелёный → exit 1; успех → 0/пусто.

| Команда | Аргументы | Действие | Выход |
|---|---|---|---|
| `validate` | `layout` | строгая проверка layout.json | `VALID ✓` / `INVALID:` + список, exit 0/2 |
| `skeleton` | `layout`, `-o OUT` (обяз.) | PNG-скелет структуры | `saved: PATH` |
| `prompt` | `layout`, `[-o OUT]` | детерминированный промпт из структуры | текст в stdout или файл |
| `compose` | `task`, `-o OUT` (обяз.), `--size WxH` (по умолч. 1280x720) | мозг: LLM-ярус или 8 архетипов → layout.json | `brain=llm\|datacenter\|… → OUT VALID ✓` |
| `render` | `layout`, `-o OUT` (обяз.), `-b offline\|zai`, `-s WxH` (по умолч. 1344x768) | рендер выбранным/детектированным бэкендом | `saved: PATH` |
| `pipeline` | `layout`, `--outdir DIR` (обяз.), `-b …`, `-s …` | v1-режим одним вызовом: skeleton+prompt+render+merge+отчёт | JSON-отчёт (§3.2) |
| `merge` | `a b`, `-o OUT` (обяз.) | склейка «было → стало» | `saved: PATH` |
| `critic` | `render layout` | зон-критика рендера по лейауту | JSON-отчёт (§3.1) |
| `patterns` | `[-t TASK]` | библиотека архетипов / выбор мозга для задания | список или `→ имя (N зон)` |
| `auto` | `[task]`, `--queue F`, `--outdir D`, `-b …`, `--attempts N` (3), `-s …` | полный автономный цикл: мозг→рендер→критик→рефайн | JSON-отчёт (§3.1+), кадр `pipeline_best.png` |
| `autolog` | `[outdir=demo/auto]` | пересборка AUTO-LOG.md из `__run.json` | `saved: AUTO-LOG.md` |
| `selftest` | — | 12 проверок ядра и сервера | `--- selftest 12/12`, exit 0/1 |

Примеры живого пути (все проверены 2026-11-09):
```bash
python3 revo.py validate layouts/datacenter.json
python3 revo.py compose "ночной дата-центр на 10000 GPU" -o /tmp/l.json
python3 revo.py render layouts/karaoke-stage.json -o /tmp/k.png -b offline
python3 revo.py pipeline layouts/datacenter.json --outdir /tmp/p -b offline
python3 revo.py auto "караоке клуб, магента-неон" --outdir /tmp/a --backend offline
```

## 2. HTTP-контракт `revo_server.py` (M2 — реализован, v1.1)

База: `http://127.0.0.1:8400` · тело запросов — JSON · ответы — JSON, UTF-8.
Раздача статики: `GET /`, `/app/*` и корневые файлы (`/app.js`, `/app.css` — index.html ссылается
относительно, это же требует file://-режим F-18) отдают `app/`. CORS: `Access-Control-Allow-Origin: *`,
`OPTIONS` → 204.
Умолчания реализации (v1.1): `backend` по умолчанию — `offline` (N-01); `/health.backend` отражает
`detect_backend()` ядра (наличие z-ai CLI), но эндпоинты без явного backend всегда offline;
`/api/compose` принимает опциональный `backend: offline|zai|auto`; ответы `pipeline/auto` несут
доп. поле `outdir`; `GET /api/patterns?task=…` добавляет `pick`.

| Метод+путь | Тело запроса | Ответ 200 | Ошибки |
|---|---|---|---|
| `GET /health` | — | `{"ok": true, "api": 1, "backend": "offline\|zai", "version": "…"}` | — |
| `POST /api/validate` | layout-объект | `{"valid": true, "errors": []}` | всегда 200; ошибки валидации — в `errors` |
| `POST /api/compose` | `{"task": "...", "size": [1280,720]}` | `{"layout": {...}, "brain": "…", "errors": []}` | 400 при пустом task |
| `POST /api/skeleton` | layout-объект | PNG-бинарник (`Content-Type: image/png`) | 400 + JSON ошибки валидации |
| `POST /api/prompt` | layout-объект | `{"prompt": "..."}` | 400 |
| `POST /api/render` | `{layout, "backend": "offline\|zai", "size": [W,H]}` | PNG-бинарник | 400 / 502 (бэкенд упал) |
| `POST /api/pipeline` | `{layout, "backend"}` | JSON-отчёт §3.2 + файлы во временный outdir (`paths` в ответе) | 400 |
| `POST /api/critic` | `{layout, "render_png_base64"}` | JSON-отчёт §3.1 | 400 |
| `POST /api/auto` | `{"task": "...", "attempts": 3, "backend": "offline"}` | JSON-отчёт автоцикла | 400 |
| `GET /api/patterns` | — | `{"patterns": [{"name","zones","kw":[…]}]}` | — |
| `GET /api/projects` [M4] | — | `{"projects": [{"id","name","updated"}]}` | — |
| `GET /api/projects/<id>` [M4] | — | JSON проекта | 404 |
| `POST /api/projects` [M4] | JSON проекта | `{"id": "…"}` | 400 |

Все ошибки — машиночитаемо: `{"error": "тип", "detail": ["…"]}` (F-17, detail — список).
404 на неизвестный путь, 405 на чужой метод, 400 payload_too_large при теле >128 МБ.
Сервер не пишет на диск вне `projects/` (M4) и системного tmp.

**Acceptance M2 (ЗАКРЫТО 2026-11-09):** `tests/smoke_api.sh` — **PASS=18 FAIL=0**
(11 позитивов + 6 негативов + статика); selftest 12/12 (пр. 11 — SHA256-детерминизм offline-рендера,
пр. 12 — сервер + `/health` 200); живая браузерная приёмка M3 против этого сервера — `demo/ui/`.

## 3. Машинные отчёты (числа никогда руками)

### 3.1 Отчёт критика (`critic_aN.json`, поле `verdicts` в авто-отчёте)
```json
{ "overall": 7.5, "pass": true, "zones": [{"id":"human","score":0.91,"mean_rgb":[245,158,11],"detail_std":42.0}],
  "verdicts": ["СЛАБО: 'aisle' (near): ждали #0e1424 in the center, lower part, получили rgb(18,24,40), детальность 6"] }
```
`overall = 10 · Σ(score·Z_WEIGHT[z]) / ΣZ_WEIGHT`; `pass = overall ≥ 7.5`.

### 3.2 Отчёт pipeline (`<stem>__run.json`)
```json
{ "date_utc": "…", "layout": "layouts/datacenter.json", "backend": "offline",
  "canvas": [1280, 720], "layout_elements": 7,
  "skeleton": "…__skeleton.png", "prompt": "…", "render": "…__render_offline.png",
  "pipeline": "…__pipeline_offline.png" }
```
### 3.3 Отчёт автоцикла (`demo/auto/<dir>/__run.json`)
```json
{ "date_utc": "…", "task": "караоке клуб, магента-неон и толпа", "brain": "karaoke",
  "backend": "offline", "attempts": 2, "best_attempt": 2, "score": 7.5, "pass": true,
  "verdicts": [], "pipeline": "…/pipeline_best.png", "skeleton": "…/skeleton.png",
  "layout_elements": 6 }
```
`AUTO-LOG.md` генерируется ТОЛЬКО из §3.3 (`revo.py autolog`) — правило «числа — никогда руками».
