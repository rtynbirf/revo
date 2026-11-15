#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REVO бэкенд «poster» — плагин-пример контракта F-25 (волна ×8, M5; v2 волна ×27).

Живое доказательство приёмки F-25: этот файл добавлен В РЕПОЗИТОРИЙ ЦЕЛИКОМ
БЕЗ единой правки ядра (revo.py / revo_server.py не тронуты добавлением) —
реестр видит его живьём, CLI и сервер рендерят им без дополнительной настройки.

v2 (волна ×27, PARITY №5): бэкенд ПРИНИМАЕТ РЕФЕРЕНС ЗОНЫ — если у зоны есть
опциональное `image_ref` (формат v1.1, docs/FORMAT.md §9) и файл существует,
зона получает вставку картинки (детерминированный cover-resize в bbox), а не
плоскую плашку. Референс без файла — честная плашка-заглушка «REF?» (не
молчаливый пропуск: отсутствие ассета видно на кадре).

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


def _paste_ref(base: Image.Image, dr: ImageDraw.ImageDraw, bbox, ref_path: str):
    """Вставка референса зоны (v1.1 image_ref) в bbox — детерминированно.

    Кроп-заполнение по центру (cover): кратчайшую сторону масштабируем, излишек
    режем симметрично — одинаковый (bbox, ref) → байт-в-байт одинаковый кадр.
    """
    x0, y0, x1, y1 = bbox
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    try:
        ref = Image.open(ref_path).convert("RGB")
    except Exception:  # noqa: BLE001 — реф недоступен = честная заглушка, не падение
        dr.rectangle([x0, y0, x1, y1], fill=(24, 26, 48))
        dr.text((x0 + 8, y0 + 8), "REF?", fill=(255, 80, 80))
        return
    scale = max(w / ref.width, h / ref.height)
    nw, nh = max(1, round(ref.width * scale)), max(1, round(ref.height * scale))
    ref = ref.resize((nw, nh), Image.BILINEAR)
    left = (nw - w) // 2
    top = (nh - h) // 2
    ref = ref.crop((left, top, left + w, top + h))
    base.paste(ref, (x0, y0))


def render(lay: dict, prompt: str, size: str, out: str) -> str:
    """Постер: база палитры → зоны far→near плашками/референсами → шапка промпта."""
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    pal = (lay.get("style") or {}).get("palette", {})
    base_col = _hex2rgb(pal.get("base", "#0b0f1a"))
    accent = pal.get("accent", "#14b8a6")
    img = Image.new("RGB", (w, h), base_col)
    dr = ImageDraw.Draw(img)
    for el in sorted(lay.get("elements", []), key=lambda e: {"far": 0, "mid": 1, "near": 2}[e.get("z", "mid")]):
        x0, y0, x1, y1 = el["bbox"]
        col = _hex2rgb(el.get("color", accent))
        ref = el.get("image_ref")
        if ref:
            _paste_ref(img, dr, (x0, y0, x1, y1), ref)
        else:
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
    "description": "постерный offline-рендер: плашки/референсы зон, рамки, шапка-промпт",
    "render": render,
}
