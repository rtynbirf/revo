# ARCHITECTURE: слои REVO-APP

> Часть комплекта восстановления app.reve.com: [SPEC.md](../SPEC.md) · [ROADMAP.md](../ROADMAP.md) ·
> [FORMAT.md](FORMAT.md) · [API.md](API.md) · [RESTORE-APP.md](RESTORE-APP.md)

## 1. Компоненты и их контракты

```
┌──────────────────────────────────────────────────────────────────────┐
│  UI (M3): app/index.html · app.js · app.css — vanilla, без сборки    │
│  канвас зон │ инспекторы │ кнопки цикла │ NL-инструкции │ галерея    │
└──────────────┬───────────────────────────────────────────────────────┘
               │ HTTP JSON (docs/API.md §2)          ▲ file:// демо-режим:
┌──────────────▼───────────────────────────────────────┤ работает без M2│
│  СЕРВЕР (M2): revo_server.py — stdlib http.server    └────────────────┤
│  раздача app/ + REST + файловые проекты (M4)                          │
└──────────────┬────────────────────────────────────────────────────────┘
               │ прямой вызов функций (тот же процесс)
┌──────────────▼────────────────────────────────────────────────────────┐
│  ЯДРО: revo.py (v1+v2, один файл, Python 3.9+ / Pillow)               │
│   МОЗГ:      compose_layout → llm_compose (ЯРУС 1) │ compose_layout_  │
│              patterns (ЯРУС 0: 8 архетипов + keyword-скоринг)         │
│   СТРУКТУРА: load_layout → validate → draw_skeleton → compile_prompt  │
│   РЕНДЕР:    render_offline (вектор, детерминизм) │ render_zai        │
│   ГЛАЗА:     analyze_zones (score 0..10, вердикты) → refine_layout /  │
│              refine_prompt_text → повтор                              │
│   ОТЧЁТЫ:    run_pipeline / run_auto → __run.json → autolog           │
└──────────────────────────────────────────────────────────────────────┘
               │ (только ЯРУС 1, опционально)
        z-ai CLI: chat (мозг) · image (рендер, кратно 32, напр. 1344x768)
```

Единственная жёсткая зависимость ядра — Pillow. Сервер — stdlib. UI — ноль зависимостей.
Любой слой можно использовать без остальных: CLI без сервера, UI-демо без M2 (file://),
ядро как библиотека (`import revo`).

## 2. Поток данных (канонический цикл)

1. **Задание** (текст) → `compose_layout(task, size)` → `(layout.json, brain)`.
2. **Правка структуры** (любая комбинация): UI-канвас / правка JSON / NL-инструкция → новая версия layout.
3. `validate(layout)` — битая структура не проходит дальше (exit 2 / HTTP 400).
4. `draw_skeleton` → PNG «как задумано» (человекочитаемая структура).
5. `compile_prompt` → детерминированный текст из структуры (никакой отсебятины).
6. Рендер: `render_offline` (детерминированный вектор) или адаптер бэкенда (`render_zai` сейчас, M5 — реестр).
7. `analyze_zones(render, layout)` → score 0..10 + вердикты; `pass = score ≥ 7.5`.
8. Слабо? → `refine_layout` (offline) / `refine_prompt_text` (zai) → шаг 6, максимум 3 попытки.
9. `merge(skeleton, render)` → кадр «было → стало»; отчёт `__run.json`; `autolog` пересобирает журнал.

Всё, что считает машина, лежит в машинных отчётах (`__run.json`) — люди и агенты читают
числа оттуда, никогда не вписывают руками (правило «числа — никогда руками»).

## 3. Каталоги: сейчас → целевое

```
revo/
├── revo.py                  ядро v1+v2 (сейчас; не дробим без причины)
├── revo_server.py           [M2] stdlib-сервер: API + статика app/
├── app/                     [M3] index.html · app.js · app.css (без сборки)
├── projects/                [M4] файловый бэкенд проектов (gitignored, кроме README)
├── tests/
│   └── smoke_api.sh         [M2] автосмоук API
├── layouts/                 эталонные сцены: datacenter.json, karaoke-stage.json
├── demo/
│   ├── datacenter/          демо v1: skeleton/prompt/render×2/pipeline×2/run.json
│   ├── karaoke-stage/       демо v1
│   └── auto/<дата>/         клетки AUTO-PILOT: task, layout, skeleton, renders, critic, __run.json
├── tasks/queue.txt          очередь заданий клетки (rotate по дню года)
├── docs/                    METHOD (v1) + этот комплект: ARCHITECTURE/FORMAT/API/RESTORE-APP [PARITY — M5]
├── SPEC.md ROADMAP.md       ТЗ и план восстановления
├── AGENTS.md llms.txt       правила роя иMachine-мембрана
├── AUTO-LOG.md              журнал автономных прогонов (генерируется)
├── .github/workflows/       ci.yml · auto-pilot.yml
└── .github/social-preview.png  обложка LABS №26
```

## 4. Интерфейсы между слоями (что нельзя ломать молча)

| Контракт | Где зафиксирован | Правило изменения |
|---|---|---|
| `layout.json` v1 поля и правила | [FORMAT.md](FORMAT.md) = `validate()` 1:1 | коммит с обновлением FORMAT + selftest + демо |
| CLI-подкоманды и флаги | [API.md](API.md) §1 = `main()` 1:1 | то же; удаление подкоманды запрещено без ROADMAP-записи (урок M1.5: `pipeline` потеряли и вернули) |
| HTTP JSON | [API.md](API.md) §2 (M2+M4) | версия поля `api` в `/health` (v2: +проекты M4) |
| Формат проекта revo-project/v1 | [projects/README.md](../projects/README.md) + API.md §2 | только расширение полей манифеста; импорт никогда не перезаписывает |
| `__run.json` / авто-отчёты | [API.md](API.md) §3 | только расширение полей, не переименование |
| CI-клетка | `.github/workflows/auto-pilot.yml` | изменение cron/веток — отдельным коммитом с обоснованием |

## 5. Принципы, из которых росло это решение

- **Offline-first** (N-01): каждый слой обязан жить без сети; сеть — ускоритель, не фундамент.
- **Один файл ядра**: revo.py остаётся целым, пока не станет тяжелее читаемости; дробление — отдельное решение с ROADMAP-записью.
- **Детерминизм как тест**: одинаковый вход → одинаковый байтовый выход (offline). Проверка войдёт в selftest в M2 (F-12/N-02).
- **Числа из машин**: любые «9/10», «7.5», «8.04» читаются из отчётов, не вписываются.
- **Демо = доказательство**: изменение пайплайна → перегон демо одним коммитом (AGENTS.md).
