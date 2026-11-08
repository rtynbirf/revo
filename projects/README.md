# projects/ — файловый бэкенд проектов (M4, F-22/F-23)

Этот каталог — хранилище проектов сервера `revo_server.py` (M4). Он **gitignored**:
проекты — живые данные пользователя, а не код. В git идёт только этот README.

## Формат хранения (revo-project/v1)

```
projects/
└── p-20261109-<hex6>/          id: p-<ГГГГММДД>-<hex6> (uuid4, коллизии исключены)
    ├── project.json            мета + индекс версий (см. ниже)
    └── v1/                     каталог версии, n — целое с 1, без пропусков
        ├── layout.json         валидный layout.json (FORMAT.md v1)
        ├── critic.json?        отчёт критика §3.1 (если сохраняли с критикой)
        └── render.png?         PNG рендера (если сохраняли с рендером)
```

`project.json`:
```json
{
  "id": "p-…", "name": "Караоке клуб", "created": "…", "updated": "…",
  "versions": [
    { "n": 1, "ts": "…UTC", "source": "рука|LLM|рефайн|import|…", "note": "…",
      "diff": {"added": ["…"], "removed": [], "changed": [{"id": "…", "fields": {…}}],
               "counts": {"old": 0, "new": 7}, "same": false},
      "files": ["layout.json", "critic.json", "render.png"] }
  ]
}
```

## Правила (не ломать молча — ARCHITECTURE.md §4)

1. Диф зон считает **только `revo.diff_zones`** — числа никогда руками.
2. Запись мета — атомарно (tmp + `os.replace`), под `PROJECTS_LOCK`.
3. Сервер пишет **только сюда** и в системный tmp (правило API.md §2).
4. Импорт ZIP (F-23) **никогда не перезаписывает** существующий id — создаёт новый
   (`renamed: true`). Экспорт (`GET /api/projects/<id>/export`) отдаёт ZIP с
   `revo-project.json` (манифест) + `v<n>/файлы` — байты 1:1 с диска.
5. Имена файлов внутри импортируемого ZIP проходят `_ZIP_ENTRY_RE`
   (`v<n>/<A-Za-z0-9._->`) — traversal из чужого ZIP исключён.
6. Потолок истории — 200 версий (MAX_VERSIONS в revo_server.py).
