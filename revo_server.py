#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REVO M2 — HTTP-сервер: REST по docs/API.md §2 + раздача статики app/.

Только stdlib (http.server) + уже установленное ядро (revo.py/Pillow) — ноль новых
зависимостей (N-04). Offline-first (N-01): сеть не трогается никогда, кроме случая,
когда клиент ЯВНО передал backend=zai; все значения по умолчанию — offline.

Контракт (docs/API.md §2, F-14..F-17):
  GET  /health            → {"ok": true, "api": 2, "backend": "offline|zai", "version": "…"}
  POST /api/validate      → {"valid": true|false, "errors": []}   (всегда 200)
  POST /api/compose       → {"layout": …, "brain": "…", "errors": []}
  POST /api/skeleton      → PNG-бинарник
  POST /api/prompt        → {"prompt": "…"}
  POST /api/render        → PNG-бинарник (backend=offline|zai; ошибки 400/502)
  POST /api/pipeline      → JSON-отчёт §3.2 + файлы во временном outdir
  POST /api/critic        → JSON-отчёт §3.1 (layout + render_png_base64)
  POST /api/auto          → JSON-отчёт автоцикла §3.3
  GET  /api/patterns      → {"patterns": [{"name","zones","kw":[…]}]}
M4 ПРОЕКТЫ (F-22/F-23; хранение — файлы в projects/<id>/, dиф зон — revo.diff_zones):
  GET  /api/projects                    → {"projects": [{"id","name","updated","versions"}]}
  POST /api/projects                    → {"id", "n": 1}   (тело {name?, layout, source?, note?, critic?, render_png_base64?})
  GET  /api/projects/<id>               → JSON проекта (мета + индекс версий с дифами)
  GET  /api/projects/<id>/versions/<n>  → {n, ts, source, note, diff, layout, critic?, render_png_base64?}
  POST /api/projects/<id>/versions      → {"n", "diff"}  (диф сервер считает от последней версии)
  GET  /api/projects/<id>/export        → ZIP-бинарник (revo-project/v1: манифест + v<n>/layout.json|critic.json|render.png)
  POST /api/projects/import             → {"id", "renamed", "versions"} (тело {"zip_base64": …}; импорт НИКОГДА не перезаписывает)
  GET  / и /app/*         → статика app/ (если каталог есть)
Ошибки — всегда машиночитаемый JSON {"error": "...", "detail": [...]}: 400/404/405/500/502 (F-17).
CORS для localhost: Access-Control-Allow-Origin: * (+Methods/Headers), OPTIONS → 204 (F-16).

Запуск: python3 revo_server.py --port 8400 [--host 127.0.0.1] [--projects-dir DIR] · лог — в stdout.
Артефакты пишутся только в системный tmp (tempfile.mkdtemp) и в каталог проектов
projects/ (M4, только по явным запросам проектов; по умолчанию REPO/projects, для selftest — переопределяется).
Дом: https://github.com/rtynbirf/revo · вселенная rtynbirf · 2026-11-09
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import io
import json
import re
import sys
import tempfile
import threading
import urllib.parse
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import revo  # ядро: вся логика — здесь, сервер только оркестрирует (ARCHITECTURE.md §1)
from PIL import Image

API_VERSION = 2  # v2: +M4 проекты (additive; правило ARCHITECTURE §4 — версия в /health)
REVO_VERSION = "v2-m4"
APP_DIR = REPO / "app"
TMP_ROOT = Path(tempfile.mkdtemp(prefix="revo-server-"))
MAX_BODY = 128 * 1024 * 1024  # 128 МБ — щедро для base64-рендеров
PROJECTS_DIR = REPO / "projects"      # M4: файловый бэкенд проектов (gitignored, кроме README)
PROJECTS_LOCK = threading.Lock()      # запись project.json — атомарно (tmp + os.replace)
PROJECT_FORMAT = "revo-project/v1"    # формат манифеста экспорта/импорта (F-23)
MAX_VERSIONS = 200                    # честный потолок истории одного проекта

# ЯРУС 0 по умолчанию: обязательный offline-мозг для /api/auto (без сети при backend=offline).
_BRAIN_LOCK = threading.Lock()

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
    ".map": "application/json", ".woff2": "font/woff2",
}


class ApiError(Exception):
    """Машиночитаемая ошибка контракта F-17: {"error": "...", "detail": [...]}."""

    def __init__(self, code: int, err: str, detail):
        super().__init__(err)
        self.code, self.err = code, err
        self.detail = detail if isinstance(detail, list) else [str(detail)]


# ================================================================ M4: ПРОЕКТЫ =
# Файловый бэкенд (ARCHITECTURE.md §3): projects/<id>/project.json + v<n>/…
# Ни один путь вне PROJECTS_DIR не пишется (правило API.md). Числа — только из
# машинных отчётов: диф считает revo.diff_zones, счётчики версий — из мета.

_PID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_SOURCE_RE = re.compile(r"^.{1,64}$", re.DOTALL)
_ZIP_ENTRY_RE = re.compile(r"^v(\d+)/([A-Za-z0-9._-]+)$")  # защита от traversal из чужого ZIP


def _pid_ok(pid: str) -> bool:
    return bool(_PID_RE.fullmatch(pid or "")) and ".." not in pid


def _pdir(pid: str) -> Path:
    if not _pid_ok(pid):
        raise ApiError(400, "bad_project_id", ["id проекта: " + _PID_RE.pattern])
    return PROJECTS_DIR / pid


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_meta(pid: str) -> dict:
    f = _pdir(pid) / "project.json"
    if not f.is_file():
        raise ApiError(404, "project_not_found", [f"проект не найден: {pid}"])
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        raise ApiError(500, "project_corrupt", [f"project.json не читается: {e}"])
    if not isinstance(meta, dict) or not isinstance(meta.get("versions"), list):
        raise ApiError(500, "project_corrupt", ["project.json без списка versions"])
    return meta


def _write_meta(pid: str, meta: dict):
    d = _pdir(pid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, d / "project.json")


def _new_pid() -> str:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        pid = f"p-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
        if not (PROJECTS_DIR / pid).exists():
            return pid


def _b64png(body: dict, key: str) -> bytes | None:
    """PNG из base64-поля версии (опционально); битый base64 → 400 (как /api/critic)."""
    b64 = body.get(key)
    if b64 is None:
        return None
    if not isinstance(b64, str) or not b64.strip():
        raise ApiError(400, "bad_base64", [f"{key} должен быть непустым base64 PNG"])
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ApiError(400, "bad_base64", [f"{key} не base64: {e}"])
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ApiError(400, "bad_image", [f"{key} — это не PNG (нет магии \\x89PNG)"])
    return raw


def _save_version(pid: str, n: int, layout: dict, critic=None, render_raw: bytes | None = None) -> list:
    """Пишет v<n>/layout.json (+critic.json, +render.png); возвращает список файлов версии."""
    d = _pdir(pid) / f"v{n}"
    d.mkdir(parents=True, exist_ok=True)
    files = []
    (d / "layout.json").write_text(
        json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files.append("layout.json")
    if critic is not None:
        (d / "critic.json").write_text(
            json.dumps(critic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files.append("critic.json")
    if render_raw:
        (d / "render.png").write_bytes(render_raw)
        files.append("render.png")
    return files


def _read_version(pid: str, n: int, meta: dict) -> dict:
    """Полная версия: запись мета + layout + критик + PNG (base64). n — целое ≥1."""
    if not isinstance(n, int) or n < 1:
        raise ApiError(400, "bad_version", ["номер версии — целое ≥ 1"])
    recs = [v for v in meta["versions"] if v.get("n") == n]
    if not recs:
        raise ApiError(404, "version_not_found", [f"версия {n} не найдена в {pid}"])
    rec = dict(recs[0])
    d = _pdir(pid) / f"v{n}"
    lp = d / "layout.json"
    if not lp.is_file():
        raise ApiError(500, "project_corrupt", [f"v{n}/layout.json отсутствует в {pid}"])
    try:
        rec["layout"] = json.loads(lp.read_text(encoding="utf-8"))
    except Exception as e:
        raise ApiError(500, "project_corrupt", [f"v{n}/layout.json не читается: {e}"])
    cp = d / "critic.json"
    if cp.is_file():
        try:
            rec["critic"] = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            rec["critic"] = {"error": "critic.json не читается"}
    pp = d / "render.png"
    if pp.is_file():
        rec["render_png_base64"] = base64.b64encode(pp.read_bytes()).decode("ascii")
    return rec


def _export_zip(pid: str, meta: dict) -> bytes:
    """ZIP формата revo-project/v1: revo-project.json + v<n>/файлы. Байты — 1:1 с диском."""
    buf = io.BytesIO()
    manifest = {"format": PROJECT_FORMAT, "exported": _now(), **meta}
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("revo-project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for rec in meta["versions"]:
            n = rec.get("n")
            d = _pdir(pid) / f"v{n}"
            for name in ("layout.json", "critic.json", "render.png"):
                f = d / name
                if f.is_file():
                    z.writestr(f"v{n}/{name}", f.read_bytes())
    return buf.getvalue()


def _import_zip(raw: bytes) -> dict:
    """ZIP → новый проект. Импорт НИКОГДА не перезаписывает существующий id (renamed=true)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise ApiError(400, "bad_zip", [f"это не ZIP: {e}"])
    names = set(z.namelist())
    if "revo-project.json" not in names:
        raise ApiError(400, "bad_manifest", ["в ZIP нет revo-project.json (формат revo-project/v1)"])
    try:
        man = json.loads(z.read("revo-project.json").decode("utf-8"))
    except Exception as e:
        raise ApiError(400, "bad_manifest", [f"revo-project.json не читается: {e}"])
    if man.get("format") != PROJECT_FORMAT:
        raise ApiError(400, "bad_manifest", [f"format ≠ {PROJECT_FORMAT}: {man.get('format')}"])
    vers = man.get("versions")
    if not isinstance(vers, list) or not vers:
        raise ApiError(400, "bad_manifest", ["манифест без непустого списка versions"])
    # читаем и валидируем ВСЕ версии ДО записи на диск — импорт атомарен (битый → 400, ноль следов)
    loaded = []
    for rec in vers:
        n = rec.get("n")
        if not isinstance(n, int) or n < 1:
            raise ApiError(400, "bad_manifest", [f"версия с некорректным n: {n!r}"])
        lp = f"v{n}/layout.json"
        if lp not in names:
            raise ApiError(400, "bad_manifest", [f"в ZIP нет {lp}"])
        try:
            lay = json.loads(z.read(lp).decode("utf-8"))
        except Exception as e:
            raise ApiError(400, "bad_manifest", [f"{lp} не читается: {e}"])
        errs = revo.validate(lay)
        if errs:
            raise ApiError(400, "invalid_layout", [f"{lp}: " + e for e in errs])
        rec2 = {k: rec[k] for k in ("n", "ts", "source", "note", "diff") if k in rec}
        ver_files = {}
        for x in sorted(names):
            if not x.startswith(f"v{n}/"):
                continue
            m = _ZIP_ENTRY_RE.fullmatch(x)  # только v<n>/<безопасное-имя>, никаких ../
            if not m or int(m.group(1)) != n or m.group(2).startswith("."):
                raise ApiError(400, "bad_manifest", [f"подозрительное имя в ZIP: {x}"])
            ver_files[x] = z.read(x)
        rec2["files"] = [x.split("/", 1)[1] for x in ver_files]
        loaded.append((rec2, ver_files))
    loaded.sort(key=lambda t: t[0]["n"])
    src_id = man.get("id") if _pid_ok(man.get("id") or "") else None
    pid = src_id if src_id and not (PROJECTS_DIR / src_id).exists() else _new_pid()
    renamed = pid != src_id
    with PROJECTS_LOCK:
        if (PROJECTS_DIR / pid).exists():  # гонка: id заняли между проверкой и записью
            pid, renamed = _new_pid(), True
        meta = {"id": pid, "name": (man.get("name") or "Импорт")[:120],
                "created": man.get("created") or _now(), "updated": _now(),
                "versions": [rec2 for rec2, _ in loaded]}
        for rec2, files in loaded:
            n = rec2["n"]
            for rel, data in files.items():
                target = _pdir(pid) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        _write_meta(pid, meta)
    return {"id": pid, "renamed": renamed, "versions": len(loaded)}


def _check_layout_field(body: dict) -> dict:
    lay = body.get("layout")
    if not isinstance(lay, dict):
        raise ApiError(400, "bad_request", ["поле 'layout' обязательно (JSON-объект)"])
    errs = revo.validate(lay)
    if errs:
        raise ApiError(400, "invalid_layout", errs)
    return lay


def _version_note(body: dict) -> dict:
    out = {}
    src = body.get("source", "рука")  # источник изменения: рука | LLM | рефайн | import | …
    if not isinstance(src, str) or not _SOURCE_RE.match(src):
        raise ApiError(400, "bad_source", ["'source' — строка 1..64 символов"])
    out["source"] = src
    note = body.get("note", "")
    if not isinstance(note, str) or len(note) > 300:
        raise ApiError(400, "bad_note", ["'note' — строка ≤ 300 символов"])
    out["note"] = note
    return out


# ===================================================================== ХЕНДЛЕР =

class RevoHandler(BaseHTTPRequestHandler):
    server_version = "revo/" + REVO_VERSION
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ транспорт ---
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, err: str, detail, extra: dict | None = None):
        self._json(code, {"error": err, "detail": detail if isinstance(detail, list) else [str(detail)]})
        # (заголовки extra нужны только 405; см. _not_allowed)

    def _not_allowed(self, method: str, path: str):
        self.send_response(405)
        self._cors()
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = json.dumps({"error": "method_not_allowed",
                           "detail": [f"{method} не поддержан на {path} — см. docs/API.md §2"]},
                          ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self, path: str):
        self._error(404, "not_found", [f"неизвестный путь: {path} — см. docs/API.md §2"])

    def _body(self):
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            raise ApiError(400, "bad_request", ["chunked-тела не поддерживаются — шлите с Content-Length"])
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_request", ["Content-Length не число"])
        if n < 0 or n > MAX_BODY:
            raise ApiError(400, "payload_too_large", [f"тело больше {MAX_BODY} байт"])
        raw = self.rfile.read(n) if n else b""
        if not raw:
            raise ApiError(400, "bad_request", ["пустое тело — жду JSON (docs/API.md §2)"])
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ApiError(400, "bad_json", [f"тело не JSON: {e}"])

    # ------------------------------------------------------------- примитивы --
    @staticmethod
    def _validate_or_400(lay):
        try:
            errs = revo.validate(lay)
        except Exception as e:
            raise ApiError(400, "invalid_layout", [f"layout не разборчив: {type(e).__name__}: {e}"])
        if errs:
            raise ApiError(400, "invalid_layout", errs)

    @staticmethod
    def _layout_field(body: dict) -> dict:
        lay = body.get("layout")
        if not isinstance(lay, dict):
            raise ApiError(400, "bad_request", ["поле 'layout' обязательно (JSON-объект)"])
        return lay

    @staticmethod
    def _backend(body: dict) -> str:
        be = body.get("backend", "offline")  # N-01: умолчание всегда offline
        # F-25 (M5): имена — из живого реестра (offline, zai, плагины backends/)
        import backend_registry  # noqa: PLC0415 — лениво, сервер стартует без плагинов
        if not backend_registry.known(be):
            names = ", ".join(c["name"] for c in backend_registry.list_backends())
            raise ApiError(400, "bad_backend", [f"'backend' не в реестре (доступны: {names})"])
        return be

    @staticmethod
    def _size(body: dict, default=None) -> list:
        s = body.get("size", default)
        if s is None:
            return default
        ok = (isinstance(s, list) and len(s) == 2
              and all(isinstance(v, int) and not isinstance(v, bool) and 64 <= v <= 4096 for v in s))
        if not ok:
            raise ApiError(400, "bad_size", ["'size' = [W, H] — целые 64..4096 (правила canvas)"])
        return s

    @staticmethod
    def _task(body: dict) -> str:
        task = body.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ApiError(400, "empty_task", ["поле 'task' обязательно (непустая строка)"])
        return task.strip()

    def _req_dir(self) -> Path:
        d = TMP_ROOT / f"req-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -------------------------------------------------------------- роутинг ---
    def do_OPTIONS(self):  # CORS preflight (F-16)
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        try:
            self._route_get()
        except ApiError as e:
            self._error(e.code, e.err, e.detail)
        except Exception as e:
            self._error(500, "internal", [f"{type(e).__name__}: {e}"])

    def do_POST(self):
        try:
            self._route_post()
        except ApiError as e:
            self._error(e.code, e.err, e.detail)
        except SystemExit as e:  # ядро зовёт sys.exit при падении zai-бэкенда
            self._error(502, "backend_failed", [str(e) or "рендер-бэкенд упал"])
        except Exception as e:
            self._error(500, "internal", [f"{type(e).__name__}: {e}"])

    def _route_get(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self._h_health()
        if path == "/api/patterns":
            return self._h_patterns()
        # M4: проекты — GET-роуты ПЕРЕД общей гвардией POST_ROUTES
        # (/api/projects жив и на GET — список, и на POST — создание)
        if path == "/api/projects":
            return self._h_projects_list()
        if path == "/api/projects/import":
            return self._not_allowed("GET", path)
        m = re.fullmatch(r"/api/projects/([^/]+)/versions/(\d+)", path)
        if m:
            return self._h_project_version_get(m.group(1), int(m.group(2)))
        m = re.fullmatch(r"/api/projects/([^/]+)/export", path)
        if m:
            return self._h_project_export(m.group(1))
        m = re.fullmatch(r"/api/projects/([^/]+)", path)
        if m:
            return self._h_project_get(m.group(1))
        if path in POST_ROUTES:
            return self._not_allowed("GET", path)
        if path.startswith("/api/"):
            return self._not_found(path)
        if path == "/" or path.startswith("/app"):
            return self._h_static(path)
        self._not_found(path)

    def _route_post(self):
        path = urllib.parse.urlparse(self.path).path
        if path in GET_ONLY:
            return self._not_allowed("POST", path)
        if path in POST_ROUTES:
            return POST_ROUTES[path](self)
        m = re.fullmatch(r"/api/projects/([^/]+)/versions", path)
        if m:
            return self._h_project_version_add(m.group(1))
        if path == "/" or path.startswith("/app"):
            return self._not_allowed("POST", path)
        self._not_found(path)

    # ------------------------------------------------------------ эндпоинты ---
    def _h_health(self):
        self._json(200, {"ok": True, "api": API_VERSION,
                         "backend": revo.detect_backend(), "version": REVO_VERSION})

    def _h_patterns(self):
        pats = [{"name": p["name"], "zones": len(p["elements"]), "kw": list(p["kw"])}
                for p in revo.PATTERNS]
        out = {"patterns": pats}
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        task = (q.get("task") or [""])[0].strip()
        if task:  # удобство UI: что выберет мозг для задания (как `revo.py patterns -t`)
            p = revo.pick_pattern(task)
            out["pick"] = {"name": p["name"], "zones": len(p["elements"])}
        self._json(200, out)

    def _h_validate(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался layout-объект (JSON-объект)"])
        try:
            errs = revo.validate(body)
        except Exception as e:
            errs = [f"layout не разборчив: {type(e).__name__}: {e}"]
        self._json(200, {"valid": not errs, "errors": errs})  # контракт: всегда 200

    def _h_compose(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался JSON-объект {task, size?, backend?}"])
        task = self._task(body)
        size = self._size(body, default=[1280, 720])
        be = body.get("backend", "offline")  # N-01: умолчание — офлайн-мозг (ЯРУС 0)
        if be not in ("offline", "zai", "auto"):
            raise ApiError(400, "bad_backend", ["'backend' ∈ {offline, zai, auto}"])
        if be == "offline":
            lay, brain = revo.compose_layout_patterns(task, tuple(size))
        else:  # zai/auto — ЯРУС 1 по явному согласию клиента; любая ошибка → fallback в ядре
            lay, brain = revo.compose_layout(task, tuple(size))
        try:
            errs = revo.validate(lay)
        except Exception as e:
            errs = [f"layout не разборчив: {e}"]
        self._json(200, {"layout": lay, "brain": brain, "errors": errs})

    def _h_skeleton(self):
        lay = self._body()
        if not isinstance(lay, dict):
            raise ApiError(400, "bad_request", ["ожидался layout-объект (JSON-объект)"])
        self._validate_or_400(lay)
        out = revo.draw_skeleton(lay, str(self._req_dir() / "skeleton.png"))
        self._send(200, Path(out).read_bytes(), "image/png")

    def _h_prompt(self):
        lay = self._body()
        if not isinstance(lay, dict):
            raise ApiError(400, "bad_request", ["ожидался layout-объект (JSON-объект)"])
        self._validate_or_400(lay)
        self._json(200, {"prompt": revo.compile_prompt(lay)})

    def _h_render(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался JSON-объект {layout, backend?, size?}"])
        lay = self._layout_field(body)
        self._validate_or_400(lay)
        be = self._backend(body)
        size = self._size(body) or [lay["canvas"]["w"], lay["canvas"]["h"]]
        out = str(self._req_dir() / f"render_{be}.png")
        if be == "zai":  # падение бэкенда → SystemExit → 502 в общем обработчике
            revo.render_zai(lay, out, f"{size[0]}x{size[1]}")
        elif be == "offline":
            revo.render_offline(lay, out)
            if (size[0], size[1]) != (lay["canvas"]["w"], lay["canvas"]["h"]):
                img = Image.open(out).resize((size[0], size[1]))
                img.save(out, optimize=True)
        else:  # F-25: плагин реестра — единая дверь, размер — на договоре плагина
            import backend_registry  # noqa: PLC0415
            backend_registry.render(be, lay, None, f"{size[0]}x{size[1]}", out)
        self._send(200, Path(out).read_bytes(), "image/png")

    def _h_pipeline(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался JSON-объект {layout, backend?}"])
        lay = self._layout_field(body)
        self._validate_or_400(lay)
        be = self._backend(body)
        d = self._req_dir()
        lp = d / "layout.json"
        lp.write_text(json.dumps(lay, ensure_ascii=False, indent=2), encoding="utf-8")
        outdir = d / "out"
        rep = dict(revo.run_pipeline(str(lp), str(outdir), be))  # отчёт §3.2, файлы в tmp
        rep["outdir"] = str(outdir)
        self._json(200, rep)

    def _h_critic(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался JSON-объект {layout, render_png_base64}"])
        lay = self._layout_field(body)
        self._validate_or_400(lay)
        b64 = body.get("render_png_base64")
        if not isinstance(b64, str) or not b64.strip():
            raise ApiError(400, "bad_request", ["поле 'render_png_base64' обязательно (base64 PNG)"])
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=True)
        except (binascii.Error, ValueError) as e:
            raise ApiError(400, "bad_base64", [f"render_png_base64 не base64: {e}"])
        png = self._req_dir() / "render.png"
        png.write_bytes(raw)
        try:
            rep = revo.analyze_zones(str(png), lay)  # отчёт §3.1
        except Exception as e:
            raise ApiError(400, "bad_image", [f"PNG не разобрать: {type(e).__name__}: {e}"])
        self._json(200, rep)

    def _h_auto(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался JSON-объект {task, attempts?, backend?, size?}"])
        task = self._task(body)
        attempts = body.get("attempts", 3)
        if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 10:
            raise ApiError(400, "bad_attempts", ["'attempts' — целое 1..10"])
        be = self._backend(body)
        size = self._size(body) or [1344, 768]
        d = self._req_dir()
        size_str = f"{size[0]}x{size[1]}"
        if be == "offline":
            # offline ⇒ без сети гарантированно: подменяем мозг на ЯРУС 0 на время вызова
            with _BRAIN_LOCK:
                orig = revo.compose_layout
                revo.compose_layout = lambda t, sz=(1280, 720): revo.compose_layout_patterns(t, sz)
                try:
                    rep = dict(revo.run_auto(task, str(d), be, attempts, size_str))
                finally:
                    revo.compose_layout = orig
        else:
            rep = dict(revo.run_auto(task, str(d), be, attempts, size_str))
        rep["outdir"] = str(d)
        self._json(200, rep)

    # ------------------------------------------------- M4: ПРОЕКТЫ (F-22/F-23) —
    def _h_projects_list(self):
        out = []
        if PROJECTS_DIR.is_dir():
            for d in sorted(PROJECTS_DIR.iterdir()):
                if not d.is_dir():
                    continue
                try:
                    meta = _read_meta(d.name)
                except ApiError:
                    continue  # мусор/чужой каталог молча не показываем — честно: он не проект
                out.append({"id": meta.get("id", d.name), "name": meta.get("name", ""),
                            "updated": meta.get("updated", ""),
                            "versions": len(meta.get("versions", []))})
        out.sort(key=lambda p: p["updated"], reverse=True)
        self._json(200, {"projects": out})

    def _h_projects_create(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request",
                           ["ожидался JSON-объект {name?, layout, source?, note?, critic?, render_png_base64?}"])
        lay = _check_layout_field(body)
        info = _version_note(body)
        critic = body.get("critic")
        if critic is not None and not isinstance(critic, dict):
            raise ApiError(400, "bad_critic", ["'critic' — JSON-объект отчёта §3.1"])
        render_raw = _b64png(body, "render_png_base64")
        name = body.get("name") or "Без имени"
        if not isinstance(name, str) or len(name) > 120:
            raise ApiError(400, "bad_name", ["'name' — строка ≤ 120 символов"])
        diff = revo.diff_zones({"elements": []}, lay)  # первая версия: всё добавлено
        with PROJECTS_LOCK:
            pid = _new_pid()
            files = _save_version(pid, 1, lay, critic, render_raw)
            meta = {"id": pid, "name": name, "created": _now(), "updated": _now(),
                    "versions": [dict(n=1, ts=_now(), **info, diff=diff, files=files)]}
            _write_meta(pid, meta)
        self._json(200, {"id": pid, "n": 1})

    def _h_project_get(self, pid: str):
        self._json(200, _read_meta(pid))

    def _h_project_version_get(self, pid: str, n: int):
        self._json(200, _read_version(pid, n, _read_meta(pid)))

    def _h_project_version_add(self, pid: str):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request",
                           ["ожидался JSON-объект {layout, source?, note?, critic?, render_png_base64?}"])
        lay = _check_layout_field(body)
        info = _version_note(body)
        critic = body.get("critic")
        if critic is not None and not isinstance(critic, dict):
            raise ApiError(400, "bad_critic", ["'critic' — JSON-объект отчёта §3.1"])
        render_raw = _b64png(body, "render_png_base64")
        with PROJECTS_LOCK:
            meta = _read_meta(pid)
            if len(meta["versions"]) >= MAX_VERSIONS:
                raise ApiError(400, "history_full",
                               [f"история переполнена (>{MAX_VERSIONS} версий) — экспортируйте и начните новый проект"])
            prev = _read_version(pid, meta["versions"][-1]["n"], meta)["layout"]
            diff = revo.diff_zones(prev, lay)
            n = meta["versions"][-1]["n"] + 1
            files = _save_version(pid, n, lay, critic, render_raw)
            meta["versions"].append(dict(n=n, ts=_now(), **info, diff=diff, files=files))
            meta["updated"] = _now()
            _write_meta(pid, meta)
        self._json(200, {"n": n, "diff": diff})

    def _h_project_export(self, pid: str):
        meta = _read_meta(pid)
        data = _export_zip(pid, meta)
        fname = f"revo-project-{pid}.zip"
        self._send(200, data, "application/zip",
                   {"Content-Disposition": f'attachment; filename="{fname}"'})

    def _h_project_import(self):
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", ["ожидался JSON-объект {zip_base64}"])
        b64 = body.get("zip_base64")
        if not isinstance(b64, str) or not b64.strip():
            raise ApiError(400, "bad_request", ["поле 'zip_base64' обязательно (base64 ZIP проекта)"])
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=True)
        except (binascii.Error, ValueError) as e:
            raise ApiError(400, "bad_base64", [f"zip_base64 не base64: {e}"])
        self._json(200, _import_zip(raw))

    # -------------------------------------------------------------- статика ---
    def _h_static(self, path: str):
        if not APP_DIR.is_dir():
            raise ApiError(404, "not_found",
                           ["app/ отсутствует — UI (M3) ещё не развёрнут; /health и /api/* работают"])
        if path == "/" or path in ("/app", "/app/"):
            rel = "index.html"
        elif path.startswith("/app/"):
            rel = urllib.parse.unquote(path[len("/app/"):])
        elif path.startswith("/") and "." in path.rsplit("/", 1)[-1]:
            # index.html ссылается на статику ОТНОСИТЕЛЬНО (app.js, app.css) — иначе
            # ломается демо file:// (F-18). Сервер отдаёт такие корневые пути из app/.
            rel = urllib.parse.unquote(path.lstrip("/"))
        else:
            raise ApiError(404, "not_found", [f"неизвестный путь: {path}"])
        target = (APP_DIR / rel).resolve()
        root = APP_DIR.resolve()
        if root not in target.parents or target.name.startswith("."):
            raise ApiError(404, "not_found", [f"нет такого файла: {path}"])
        if not target.is_file():
            raise ApiError(404, "not_found", [f"нет такого файла: {path}"])
        self._send(200, target.read_bytes(), MIME.get(target.suffix.lower(), "application/octet-stream"))

    # ------------------------------------------------------------------ лог ---
    def log_message(self, fmt, *args):
        print(f"[{dt.datetime.now():%H:%M:%S}] {self.address_string()} {fmt % args}", flush=True)


POST_ROUTES = {
    "/api/validate": RevoHandler._h_validate,
    "/api/compose": RevoHandler._h_compose,
    "/api/skeleton": RevoHandler._h_skeleton,
    "/api/prompt": RevoHandler._h_prompt,
    "/api/render": RevoHandler._h_render,
    "/api/pipeline": RevoHandler._h_pipeline,
    "/api/critic": RevoHandler._h_critic,
    "/api/auto": RevoHandler._h_auto,
    "/api/projects": RevoHandler._h_projects_create,
    "/api/projects/import": RevoHandler._h_project_import,
}
GET_ONLY = {"/health", "/api/patterns"}


# ======================================================================= main =

def main():
    global PROJECTS_DIR
    ap = argparse.ArgumentParser(
        prog="revo_server",
        description="REVO M2+M4: HTTP-сервер (stdlib only) — REST docs/API.md §2 + статика app/ + проекты")
    ap.add_argument("--port", type=int, default=8400, help="порт (по умолчанию 8400)")
    ap.add_argument("--host", default="127.0.0.1", help="хост (по умолчанию 127.0.0.1)")
    ap.add_argument("--projects-dir", default=str(PROJECTS_DIR),
                    help="каталог файловых проектов (по умолчанию ./projects; selftest указывает tmp)")
    args = ap.parse_args()
    PROJECTS_DIR = Path(args.projects_dir).resolve()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    srv = ThreadingHTTPServer((args.host, args.port), RevoHandler)
    app_state = "есть" if APP_DIR.is_dir() else "нет (UI M3 ещё не развёрнут — /health и API живут)"
    print(f"REVO server v{REVO_VERSION} · http://{args.host}:{args.port} · app/: {app_state} · проекты: {PROJECTS_DIR}", flush=True)
    print(f"ядро: revo.py (backend-детект: {revo.detect_backend()}) · умолчание запросов: offline (N-01) "
          f"· tmp-каталог артефактов: {TMP_ROOT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("…остановлен (Ctrl-C)", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
