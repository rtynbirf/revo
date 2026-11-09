# Рендер-бэкенды REVO — договор плагина (F-25, M5)

> Реестр: `backend_registry.py`. Каталог плагинов: `backends/`.
> **Главный закон F-25: новый бэкенд = один файл в `backends/` — ядро
> (`revo.py`, `revo_server.py`) НЕ правится.** Реестр видит плагин живьём.

## Договор

Файл `backends/<имя>.py` определяет словарь `BACKEND`:

```python
def render(lay: dict, prompt: str, size: str, out: str) -> str:
    """lay — валидный layout (revo.validate(lay) == []);
    prompt — скомпилированный промпт сцены;
    size   — "WxH" (можно игнорировать, если рендерится в canvas);
    out    — куда писать PNG; вернуть путь."""
    ...

BACKEND = {
    "name": "poster",       # = имя файла; канон ^[a-z][a-z0-9-]{0,30}$
    "kind": "offline",      # offline | network (N-01: умолчание всегда offline)
    "description": "…",     # одна строка — идёт в таблицу `revo.py backends`
    "render": render,
}
```

## Правила честности

- Имя плагина = имени файла; имена `offline`/`zai` зарезервированы за ядром.
- Сломанный плагин не прячется: в реестре он виден с `kind=broken` и текстом
  ошибки, вызов — честный ФАКТ-отказ (угадчик — яд).
- `offline`-бэкенды обязаны быть детерминированными (N-02): одинаковый layout
  → байт-в-байт одинаковый PNG (приёмка 16 selftest).
- Сеть — только `kind: network` (N-01); сервер по умолчанию зовёт offline.

## Двери реестра

| Дверь | Что делает |
|---|---|
| `python3 revo.py backends` | живой срез реестра (имя, OFF/NET/⛔, описание, источник) |
| `python3 revo.py render l.json -o r.png -b poster` | рендер любым бэкендом из реестра |
| `python3 revo.py pipeline l.json --outdir d -b poster` | пайплайн любым бэкендом (имя в `__run.json`) |
| `POST /api/render {"backend": "poster", …}` | сервер рендерит плагином; чужое имя → `400 bad_backend` со списком реестра |
| `backend_registry.list_backends() / get_backend(n) / render(n, …)` | программный доступ |

## Встроенные

| Имя | kind | Что |
|---|---|---|
| `offline` | offline | детерминированный offline-вектор (N-02), ноль сети |
| `zai` | network | z-ai image (ЯРУС 1: сеть/ключи опциональны) |

## Плагины

| Имя | kind | Что |
|---|---|---|
| `poster` | offline | постерный рендер: плоские плашки, рамки, типографская шапка-промпт |

## Приёмка F-25 (пройдена 2026-11-09, волна ×8)

- selftest 21/21: п.15 (реестр живьём: offline+zai+poster), п.16 (плагин
  рендерит + SHA256-детерминизм), п.17 (чужое имя → честный ФАКТ-отказ),
  п.18 (pipeline с плагином, имя бэкенда в отчёте), п.21 (сервер: плагин →
  200 PNG, чужое имя → 400 bad_backend);
- смоук 36/0: `backend=poster` → 200 image/png; чужой бэкенд → 400 bad_backend;
- плагин `backends/poster.py` добавлен одним файлом, ноль правок ядра ради него.
