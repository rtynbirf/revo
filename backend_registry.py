#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Реестр рендер-бэкендов REVO (F-25, M5, волна ×8).

ДОГОВОР ПЛАГИНА (docs/BACKENDS.md): файл `backends/<name>.py` определяет

    BACKEND = {
        "name": "poster",            # = имя файла, ASCII/цифры/дефис
        "kind": "offline",           # offline | network (N-01: умолчание всегда offline)
        "description": "…",          # одна строка — идёт в таблицу `revo.py backends`
        "render": render,            # def render(lay, prompt, size, out) -> str (путь)
    }

 render(lay: dict, prompt: str, size: str, out: str) -> str
   lay    — валидный layout (revo.validate(lay) == []);
   prompt — скомпилированный промпт сцены (revo.compile_prompt);
   size   — "WxH" (бэкенд волен игнорировать, если рендерит в canvas);
   out    — куда писать PNG; вернуть путь.

НОВЫЙ БЭКЕНД = файл в backends/ — ядро (revo.py/revo_server.py) НЕ правится.
Приёмка F-25: drop-in без правки ядра, реестр видит его живьём.

Сломанный плагин — честный: в списке виден с kind="broken" и ошибкой,
render по нему — sys.exit с ФАКТом (угадчик — яд; тихих пропусков нет).

Встроенные: offline (детерминированный вектор, N-02) и zai (сеть, ЯРУС 1).
Встроенные имена резервируются за ядром — плагин с таким именем отклоняется.
"""
from __future__ import annotations

import importlib.util
import re as _re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGINS_DIR = ROOT / "backends"
NAME_RE = _re.compile(r"^[a-z][a-z0-9-]{0,30}$")
BUILTIN_NAMES = ("offline", "zai")


def _revo():
    """Ленивый импорт ядра (разрывает круг: revo ↔ backend_registry)."""
    import revo  # noqa: PLC0415
    return revo


def _render_offline(lay, prompt, size, out):
    return _revo().render_offline(lay, out)


def _render_zai(lay, prompt, size, out):
    return _revo().render_zai(lay, out, size, prompt)


def _builtins():
    return {
        "offline": {"name": "offline", "kind": "offline", "source": "builtin",
                    "description": "детерминированный offline-вектор (N-02)",
                    "render": _render_offline},
        "zai": {"name": "zai", "kind": "network", "source": "builtin",
                "description": "z-ai image (ЯРУС 1: сеть/ключи опциональны)",
                "render": _render_zai},
    }


def _load_plugin(path: Path):
    """Один файл-плагин → карточка бэкенда или честная карточка «broken»."""
    mod_name = f"revo_backend_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        card = getattr(mod, "BACKEND")
        if not isinstance(card, dict) or not callable(card.get("render")):
            raise ValueError("BACKEND.render не вызываемый (или BACKEND не словарь)")
        name = str(card.get("name", ""))
        if name != path.stem:
            raise ValueError(f"BACKEND.name={name!r} != имени файла {path.stem!r}")
        if not NAME_RE.match(name):
            raise ValueError(f"имя {name!r} вне канона {NAME_RE.pattern}")
        if name in BUILTIN_NAMES:
            raise ValueError(f"имя {name!r} зарезервировано за ядром")
        kind = card.get("kind", "offline")
        if kind not in ("offline", "network"):
            raise ValueError(f"kind={kind!r} ∉ {{offline, network}}")
        return {"name": name, "kind": kind, "source": str(path.relative_to(ROOT)),
                "description": str(card.get("description", "")),
                "render": card["render"]}
    except Exception as e:  # честный broken, не тихий пропуск
        return {"name": path.stem, "kind": "broken", "source": str(path.relative_to(ROOT)),
                "description": f"СЛОМАН: {type(e).__name__}: {e}", "render": None}


def _plugins():
    if not PLUGINS_DIR.is_dir():
        return {}
    out = {}
    for f in sorted(PLUGINS_DIR.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        card = _load_plugin(f)
        out[card["name"]] = card
    return out


def list_backends() -> list:
    """Все бэкенды (встроенные + плагины) — машины, не настроение."""
    cards = list(_builtins().values())
    cards += [c for n, c in sorted(_plugins().items())]
    return cards


def known(name) -> bool:
    return isinstance(name, str) and name in _builtins() or name in _plugins()


def get_backend(name: str) -> dict:
    """Карточка бэкенда по имени; неизвестное/сломанное — честный отказ."""
    card = _builtins().get(name) or _plugins().get(name)
    if card is None:
        names = ", ".join(sorted(set(_builtins()) | set(_plugins())))
        sys.exit(f"REVO: бэкенд {name!r} не в реестре (доступны: {names}) — "
                 f"факт, не угадка")
    if card.get("render") is None:
        sys.exit(f"REVO: бэкенд {name!r} сломан ({card['description']}) — чини плагин")
    return card


def render(name: str, lay: dict, prompt: str | None = None,
           size: str = "1344x768", out: str = "render.png") -> str:
    """Единая дверь рендера: имя → бэкенд → PNG на диске (возвращает путь)."""
    return get_backend(name)["render"](lay, prompt, size, out)
