# REVO — дом прототипа метода Reve: layout → render

![social-preview](.github/social-preview.png)

**REVO** (REnder-Via-layout-Orchestrator) — runnable-прототип метода **Reve «The Layout Bet»**:
сложная сцена собирается не «промптом-лотереей», а в два этапа — сначала **структура** (layout:
зоны, координаты, свет, палитра), потом **рендер**, детерминированно собранный из этой структуры.

Здесь метод живёт как **код, который можно запустить**, а не только как демо-картинки.

---

## Откуда метод

Из нашего архива отчёта **«Training Reve 2.0 at scale»** (app.reve.com/training-infra):
Reve строила генерацию на ставке «layout вместо свободного текста» — сцена описывается
структурой, и рендер обязан ей подчиняться. Полное зеркало отчёта и журнал поиска исходников:
[`rtynbirf/polymath-gen/docs/REVE_METHOD.md`](https://github.com/rtynbirf/polymath-gen/blob/main/docs/REVE_METHOD.md)
(архив: `reve-training-infra.zip`, 41 файл).

## Пайплайн

```
layout.json ──► [1] СКЕЛЕТ      структурный план: рамки зон, подписи, точка схода
                [2] ПРОМПТ      детерминированный рендер-промпт ИЗ структуры
                [3] РЕНДЕР      offline (вектор) или zai (генерация по промпту)
                [4] СКЛЕЙКА     «было → стало» одним кадром
```

## Быстрый старт

```bash
pip install pillow          # единственная зависимость

python3 revo.py validate layouts/datacenter.json
python3 revo.py pipeline layouts/datacenter.json --outdir demo/datacenter -b offline
python3 revo.py pipeline layouts/datacenter.json --outdir demo/datacenter -b zai   # если есть z-ai CLI
```

Готово: в `demo/datacenter/` появятся `*__skeleton.png`, `*__render_*.png`, `*__pipeline_*.png`,
`*__prompt.txt`, `*__run.json`.

## Формат layout

```jsonc
{
  "canvas": { "w": 1280, "h": 720 },
  "style": {
    "mood": "photorealistic cinematic…",
    "palette": { "base": "#0b0f1a", "primary": "#2f6fed", "accent": "#14b8a6" },
    "light": "cold LED glow, warm key on the human",
    "perspective": [0.5, 0.47]              // точка схода, доли полотна
  },
  "elements": [
    { "id": "racks-left", "bbox": [30,150,420,650], "z": "mid",
      "role": "server racks with blue LEDs, receding", "color": "#2f6fed", "glow": true }
  ]
}
```

`z` — глубина: `far / mid / near` (порядок отрисовки и растворение в фоне).
`revo.py validate` проверяет схему до рендера: «числа — не руками».

## Демо (прогон 2026-11-09, оба бэкенда живые)

| Сцена | Offline (детерминированный) | Z-AI (генерация по промпту) |
|---|---|---|
| Дата-центр 10k GPU | [pipeline_offline](demo/datacenter/datacenter__pipeline_offline.png) | [pipeline_zai](demo/datacenter/datacenter__pipeline_zai.png) |
| Караоке-сцена СИНГУЛЯРА | [pipeline_offline](demo/karaoke-stage/karaoke-stage__pipeline_offline.png) | [pipeline_zai](demo/karaoke-stage/karaoke-stage__pipeline_zai.png) |

Рендер обязан следовать скелету: перспектива в точке схода, стойки слева/справа,
LED-стена в глубине, инженер в оранжевом жилете в центральном проходе.

## Бэкенды

| Бэкенд | Что делает | Зависимости |
|---|---|---|
| `offline` | векторный рендер: градиент, зоны far→near с растворением, свечение, виньетка. Детерминирован — тот же layout всегда даёт тот же кадр | только Pillow |
| `zai` | генерация: скомпилированный промпт → `z-ai image` (размер кратен 32, напр. `1344x768`) | `z-ai` CLI в PATH |

## Честный статус

- ✅ пайплайн layout → skeleton → prompt → render → merge работает (прогоны в `demo/`);
- ✅ валидатор схемы ловит битые bbox/z/полотно до рендера;
- ⚙️ LLM-составитель layout (текст задания → JSON) живёт в старшем репо
  [`polymath-gen`](https://github.com/rtynbirf/polymath-gen) (PromptEngine) — REVO сознательно
  держит только ядро метода, без оркестратора-критика;
- 🖐 загрузка social-preview в настройки репо — только руками в UI (GitHub API это запрещает).

## Связи во вселенной

- [`polymath-gen`](https://github.com/rtynbirf/polymath-gen) — полный медиа-агент (планировщик,
  промпт-движок, критик-глаза, эволюция); REVO — его метод-ядро как отдельный инструмент;
- [`rtynbirf/rtynbirf`](https://github.com/rtynbirf/rtynbirf) → `cells/` — те же идеи клетками роя;
- [`polimat-hub`](https://github.com/rtynbirf/polimat-hub) — место сверок роя (TODO/КОСЯКИ/ОТЧЁТЫ).

---
*REVO · серия RTYNBIRF LABS №26 · MIT · 2026-11-09. Сначала структура — потом картинка.*
