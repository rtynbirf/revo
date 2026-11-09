#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REVO бэкенд «poster» — плагин-пример контракта F-25 (волна ×8, M5).

Живое доказательство приёмки F-25: этот файл добавлен В РЕПОЗИТОРИЙ ЦЕЛИКОМ
БЕЗ единой правки ядра (revo.py / revo_server.py не тронуты добавлением) —
реестр видит его живьём, CLI и сервер рендерят им без дополнительной настройки.

Постерный рендер: плоские цвета, жёсткие рамки зон, типографская шапка.
Offline (N-01), детерминированный (N-02): одинаковый layout → байт-в-байт
одинаковый PNG. Зависимости: Pillow (уже у ядра).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def _hex2rgb(h: str):
    h = (h or "#0b0f1a").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render(lay: dict, prompt: str, size: str, out: str) -> str:
    """Постер: база палитры → зоны far→near плоскими плашками → шапка промпта."""
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    pal = (lay.get("style") or {}).get("palette", {})
    base = _hex2rgb(pal.get("base", "#0b0f1a"))
    accent = pal.get("accent", "#14b8a6")
    img = Image.new("RGB", (w, h), base)
    dr = ImageDraw.Draw(img)
    for el in sorted(lay.get("elements", []), key=lambda e: {"far": 0, "mid": 1, "near": 2}[e.get("z", "mid")]):
        x0, y0, x1, y1 = el["bbox"]
        col = _hex2rgb(el.get("color", accent))
        dr.rectangle([x0, y0, x1, y1], fill=col)
        dr.rectangle([x0, y0, x1, y1], outline=_hex2rgb(accent), width=6)
        dr.rectangle([x0, y0, x1, min(y1, y0 + 10)], fill=(255, 255, 255))
    # типографская шапка: первые слова промпта капсом (детерминированно)
    words = " ".join((prompt or "REVO POSTER").split()[:6]).upper()
    dr.rectangle([0, 0, w, 34], fill=_hex2rgb(accent))
    dr.text((12, 10), words[: int(w / 9)], fill=(0, 0, 0))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


BACKEND = {
    "name": "poster",
    "kind": "offline",
    "description": "постерный offline-рендер: плоские плашки, рамки, шапка-промпт",
    "render": render,
}
