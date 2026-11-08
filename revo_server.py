#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REVO M2 — HTTP-сервер: REST по docs/API.md §2 + раздача статики app/.

Только stdlib (http.server) + уже установленное ядро (revo.py/Pillow) — ноль новых
зависимостей (N-04). Offline-first (N-01): сеть не трогается никогда, кроме случая,
когда клиент ЯВНО передал backend=zai; все значения по умолчанию — offline.

Контракт (docs/API.md §2, F-14..F-17):
  GET  /health            → {"ok": true, "api": 1, "backend": "offline|zai", "version": "…"}
  POST /api/validate      → {"valid": true|false, "errors": []}   (всегда 200)
  POST /api/compose       → {"layout": …, "brain": "…", "errors": []}
  POST /api/skeleton      → PNG-бинарник
  POST /api/prompt        → {"prompt": "…"}
  POST /api/render        → PNG-бинарник (backend=offline|zai; ошибки 400/502)
  POST /api/pipeline      → JSON-отчёт §3.2 + файлы во временном outdir
  POST /api/critic        → JSON-отчёт §3.1 (layout + render_png_base64)
  POST /api/auto          → JSON-отчёт автоцикла §3.3
  GET  /api/patterns      → {"patterns": [{"name","zones","kw":[…]}]}
  GET  / и /app/*         → статика app/ (если каталог есть)
Ошибки — всегда машиночитаемый JSON {"error": "...", "detail": [...]}: 400/404/405/500/502 (F-17).
CORS для localhost: Access-Control-Allow-Origin: * (+Methods/Headers), OPTIONS → 204 (F-16).

Запуск: python3 revo_server.py --port 8400 [--host 127.0.0.1] · лог — в stdout.
Артефакты пишутся только в системный tmp (tempfile.mkdtemp), вне его — ничего (правило API.md).
Дом: https://github.com/rtynbirf/revo · вселенная rtynbirf · 2026-11-09
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import re
import sys
import tempfile
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import revo  # ядро: вся логика — здесь, сервер только оркестрирует (ARCHITECTURE.md §1)
from PIL import Image

API_VERSION = 1
REVO_VERSION = "v2-m2"
APP_DIR = REPO / "app"
TMP_ROOT = Path(tempfile.mkdtemp(prefix="revo-server-"))
MAX_BODY = 128 * 1024 * 1024  # 128 МБ — щедро для base64-рендеров

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
        if be not in ("offline", "zai"):
            raise ApiError(400, "bad_backend", ["'backend' ∈ {offline, zai}"])
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
        if path in POST_ROUTES:
            return self._not_allowed("GET", path)
        if path == "/health":
            return self._h_health()
        if path == "/api/patterns":
            return self._h_patterns()
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
        else:
            revo.render_offline(lay, out)
            if (size[0], size[1]) != (lay["canvas"]["w"], lay["canvas"]["h"]):
                img = Image.open(out).resize((size[0], size[1]))
                img.save(out, optimize=True)
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
}
GET_ONLY = {"/health", "/api/patterns"}


# ======================================================================= main =

def main():
    ap = argparse.ArgumentParser(
        prog="revo_server",
        description="REVO M2: HTTP-сервер (stdlib only) — REST docs/API.md §2 + статика app/")
    ap.add_argument("--port", type=int, default=8400, help="порт (по умолчанию 8400)")
    ap.add_argument("--host", default="127.0.0.1", help="хост (по умолчанию 127.0.0.1)")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), RevoHandler)
    app_state = "есть" if APP_DIR.is_dir() else "нет (UI M3 ещё не развёрнут — /health и API живут)"
    print(f"REVO server v{REVO_VERSION} · http://{args.host}:{args.port} · app/: {app_state}", flush=True)
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
