#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REVO v2 — REnder-Via-layout-Orchestrator. АВТОНОМНЫЙ И САМОДОСТАТОЧНЫЙ.

Метод Reve («The Layout Bet»): сначала LAYOUT (структура сцены), потом RENDER.

Автономия — два яруса, никогда не застревает:
  ЯРУС 1 (если в PATH есть z-ai):  мозг = LLM (z-ai chat), рендер = z-ai image
  ЯРУС 0 (без сети и ключей):      мозг = встроенный композитор паттернов,
                                   глаза = встроенный зон-критик (PIL-анализ),
                                   рендер = детерминированный offline-вектор

Пайплайн auto: задание → layout → skeleton → prompt → render → критика зон
             → (слабые зоны? усиление промпта/лейаута → снова) → лучший кадр → отчёт

Зависимости ядра: Python 3.9+, Pillow. Всё остальное — опционально и детектируется само.
Дом: https://github.com/rtynbirf/revo · вселенная rtynbirf · 2026-11-09
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("REVO требует Pillow: pip install pillow")

Z_ORDER = {"far": 0, "mid": 1, "near": 2}
Z_FADE = {"far": 0.62, "mid": 0.30, "near": 0.0}
Z_WEIGHT = {"far": 0.8, "mid": 1.0, "near": 1.2}
PASS_SCORE = 7.5  # порог приёмки критика

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
MONO = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]

# ============================================================ ПАТТЕРН-МОЗГ ===
# Библиотека архетипов сцен: keyword-скоринг задания → лейаут. Работает offline.

PATTERNS = [
    {
        "name": "datacenter",
        "kw": ["datacenter", "data-center", "датацентр", "дата-центр", "сервер", "server",
               "gpu", "rack", "стойк", "хостинг", "кластер", "cluster", "10k", "10000"],
        "palette": {"base": "#0b0f1a", "primary": "#2f6fed", "accent": "#14b8a6", "warm": "#f59e0b"},
        "mood": "photorealistic cinematic wide shot of a huge datacenter hall at night",
        "light": "cold electric-blue LED glow from server rows, teal wall in the deep background, single warm key light on the human figure, polished floor reflections",
        "perspective": [0.5, 0.47],
        "elements": [
            {"id": "led-wall", "bbox": [0.336, 0.25, 0.664, 0.6], "z": "far", "color": "#14b8a6", "glow": True,
             "role": "teal LED status wall glowing in the deep background"},
            {"id": "racks-left", "bbox": [0.023, 0.208, 0.328, 0.9], "z": "mid", "color": "#2f6fed", "glow": True,
             "role": "long rows of dark server racks with rows of tiny blue LEDs, receding"},
            {"id": "racks-right", "bbox": [0.672, 0.208, 0.977, 0.9], "z": "mid", "color": "#2f6fed", "glow": True,
             "role": "long rows of dark server racks with rows of tiny blue LEDs, receding, mirrored"},
            {"id": "ceiling", "bbox": [0.047, 0.055, 0.953, 0.18], "z": "far", "color": "#22304f",
             "role": "dark industrial ceiling with long thin light strips"},
            {"id": "floor", "bbox": [0.0, 0.78, 1.0, 1.0], "z": "near", "color": "#101726",
             "role": "polished dark reflective floor mirroring the LED rows"},
            {"id": "human", "bbox": [0.46, 0.555, 0.547, 0.85], "z": "near", "color": "#f59e0b", "glow": True,
             "role": "lone engineer in an orange safety vest seen from behind, holding a tablet"},
        ],
    },
    {
        "name": "karaoke",
        "kw": ["karaoke", "караоке", "сцена", "stage", "concert", "концерт", "песн", "song",
               "микрофон", "microphone", "neon party", "клуб", "club", "музык", "music"],
        "palette": {"base": "#120b1c", "primary": "#a855f7", "accent": "#ec4899", "warm": "#fbbf24"},
        "mood": "photorealistic cinematic shot of an underground karaoke stage",
        "light": "violet and magenta neon wash, warm spotlight cone through haze, lyric glow reflecting on the floor",
        "perspective": [0.5, 0.55],
        "elements": [
            {"id": "lyric-screen", "bbox": [0.187, 0.083, 0.812, 0.458], "z": "far", "color": "#a855f7", "glow": True,
             "role": "huge glowing karaoke lyric screen with large luminous words"},
            {"id": "stage", "bbox": [0.062, 0.458, 0.937, 0.777], "z": "mid", "color": "#ec4899",
             "role": "wide dark stage platform with edge neon strip"},
            {"id": "spotlight", "bbox": [0.406, 0.055, 0.593, 0.527], "z": "mid", "color": "#fbbf24", "glow": True,
             "role": "warm spotlight cone cutting through the haze onto the singer spot"},
            {"id": "microphone", "bbox": [0.468, 0.486, 0.531, 0.72], "z": "near", "color": "#fbbf24",
             "role": "vintage microphone on a stand at the center of the stage"},
            {"id": "crowd", "bbox": [0.0, 0.777, 1.0, 1.0], "z": "near", "color": "#1a1030",
             "role": "silhouettes of the crowd with raised hands, backlit by the stage"},
            {"id": "neon-sign", "bbox": [0.047, 0.097, 0.172, 0.277], "z": "mid", "color": "#ec4899", "glow": True,
             "role": "pink neon sign on the side wall"},
        ],
    },
    {
        "name": "cyber-city",
        "kw": ["city", "город", "неон", "neon", "cyberpunk", "киберпанк", "улиц", "street",
               "мегаполис", "megapolis", "дождь", "rain", "отражени", "skyline"],
        "palette": {"base": "#0a0e18", "primary": "#06b6d4", "accent": "#f43f5e", "warm": "#fbbf24"},
        "mood": "photorealistic cinematic cyberpunk megacity street at night after rain",
        "light": "cyan and magenta neon signage reflections in wet asphalt, volumetric fog, cold street lamps",
        "perspective": [0.5, 0.5],
        "elements": [
            {"id": "towers-far", "bbox": [0.05, 0.08, 0.95, 0.45], "z": "far", "color": "#164e63",
             "role": "dense skyscraper silhouettes with tiny lit windows fading in fog"},
            {"id": "signs-left", "bbox": [0.02, 0.25, 0.3, 0.7], "z": "mid", "color": "#f43f5e", "glow": True,
             "role": "stacked neon advertisement signs in a foreign alphabet"},
            {"id": "signs-right", "bbox": [0.7, 0.25, 0.98, 0.7], "z": "mid", "color": "#06b6d4", "glow": True,
             "role": "stacked cyan holographic billboards"},
            {"id": "street", "bbox": [0.0, 0.72, 1.0, 1.0], "z": "near", "color": "#0e1726",
             "role": "wet asphalt street mirroring the neon"},
            {"id": "figure", "bbox": [0.44, 0.5, 0.56, 0.9], "z": "near", "color": "#fbbf24", "glow": True,
             "role": "lone figure with a transparent umbrella seen from behind"},
        ],
    },
    {
        "name": "cozy-room",
        "kw": ["cozy", "уютн", "комнат", "room", "интерьер", "interior", "дом", "home",
               "ламп", "lamp", "книг", "book", "чтение", "reading"],
        "palette": {"base": "#1a1410", "primary": "#d97706", "accent": "#84cc16", "warm": "#fcd34d"},
        "mood": "photorealistic cozy interior scene, soft cinematic warmth",
        "light": "warm lamp pools of light, soft shadows, dust motes in the air, gentle window glow",
        "perspective": [0.5, 0.52],
        "elements": [
            {"id": "window", "bbox": [0.65, 0.12, 0.92, 0.55], "z": "far", "color": "#fcd34d", "glow": True,
             "role": "tall window with dawn light and sheer curtains"},
            {"id": "bookshelf", "bbox": [0.03, 0.15, 0.35, 0.72], "z": "mid", "color": "#7c4a12",
             "role": "tall wooden bookshelf packed with books"},
            {"id": "armchair", "bbox": [0.38, 0.45, 0.62, 0.8], "z": "near", "color": "#b45309",
             "role": "worn leather armchair with a knitted blanket"},
            {"id": "lamp", "bbox": [0.62, 0.35, 0.72, 0.75], "z": "near", "color": "#fcd34d", "glow": True,
             "role": "warm floor lamp with fabric shade"},
            {"id": "rug", "bbox": [0.25, 0.78, 0.8, 0.98], "z": "near", "color": "#57534e",
             "role": "soft round rug on wooden floor"},
        ],
    },
    {
        "name": "mountains",
        "kw": ["mountain", "гор", "пейзаж", "landscape", "рассвет", "sunset", "sunrise",
               "долин", "valley", "туман", "mist", "fog", "природ", "nature", "альп"],
        "palette": {"base": "#101820", "primary": "#38bdf8", "accent": "#fb923c", "warm": "#fde68a"},
        "mood": "photorealistic epic mountain landscape at dawn, cinematic scale",
        "light": "first sun rays kissing the peaks, cold blue valley mist, golden rim light",
        "perspective": [0.5, 0.42],
        "elements": [
            {"id": "peaks-far", "bbox": [0.0, 0.1, 1.0, 0.45], "z": "far", "color": "#334155",
             "role": "distant snow-capped mountain range glowing with first light"},
            {"id": "peaks-mid", "bbox": [0.0, 0.3, 1.0, 0.6], "z": "mid", "color": "#1e293b",
             "role": "closer dark rocky ridges"},
            {"id": "mist", "bbox": [0.0, 0.5, 1.0, 0.72], "z": "mid", "color": "#94a3b8", "glow": True,
             "role": "river of soft mist filling the valley"},
            {"id": "foreground-rock", "bbox": [0.0, 0.7, 0.45, 1.0], "z": "near", "color": "#0f172a",
             "role": "dark rocky slope with pine silhouettes"},
            {"id": "sun", "bbox": [0.68, 0.16, 0.82, 0.3], "z": "far", "color": "#fb923c", "glow": True,
             "role": "rising sun disc in golden haze"},
        ],
    },
    {
        "name": "studio",
        "kw": ["studio", "студи", "product", "продукт", "предмет", "object", "shot",
               "реклам", "gadget", "гаджет", "микрофон", "microphone", "часы", "watch"],
        "palette": {"base": "#111114", "primary": "#e5e7eb", "accent": "#3b82f6", "warm": "#f59e0b"},
        "mood": "clean photorealistic studio product shot, commercial quality",
        "light": "large soft key from upper left, rim light separating subject, gradient backdrop, subtle floor reflection",
        "perspective": [0.5, 0.55],
        "elements": [
            {"id": "backdrop", "bbox": [0.0, 0.0, 1.0, 0.8], "z": "far", "color": "#1f2126",
             "role": "seamless gradient studio backdrop"},
            {"id": "subject", "bbox": [0.34, 0.3, 0.66, 0.72], "z": "near", "color": "#e5e7eb", "glow": True,
             "role": "hero product on a pedestal, perfectly centered"},
            {"id": "pedestal", "bbox": [0.3, 0.72, 0.7, 0.88], "z": "near", "color": "#2a2d33",
             "role": "matte cylinder pedestal"},
            {"id": "reflection", "bbox": [0.3, 0.88, 0.7, 1.0], "z": "near", "color": "#17181c",
             "role": "subtle mirror reflection of the product"},
        ],
    },
    {
        "name": "space",
        "kw": ["space", "космос", "станци", "station", "орбит", "orbit", "звезд", "star",
               "астероид", "asteroid", "планет", "planet", "nasa", "ракет", "rocket"],
        "palette": {"base": "#05060d", "primary": "#818cf8", "accent": "#22d3ee", "warm": "#fbbf24"},
        "mood": "photorealistic deep space scene, hard cinematic sci-fi",
        "light": "hard single sun key from the side, planet-shine fill, star field, cold rim lights",
        "perspective": [0.42, 0.45],
        "elements": [
            {"id": "starfield", "bbox": [0.0, 0.0, 1.0, 1.0], "z": "far", "color": "#0b0d1a",
             "role": "dense star field with faint milky-way band"},
            {"id": "planet", "bbox": [0.6, 0.08, 1.15, 0.75], "z": "far", "color": "#3b4a8f", "glow": True,
             "role": "large ringed planet partially lit by the sun"},
            {"id": "station", "bbox": [0.15, 0.35, 0.62, 0.68], "z": "mid", "color": "#818cf8", "glow": True,
             "role": "modular space station with solar panels and lit windows"},
            {"id": "asteroids", "bbox": [0.0, 0.6, 0.75, 1.0], "z": "near", "color": "#20242f",
             "role": "scattered asteroids with sun-lit edges"},
        ],
    },
    {
        "name": "cabin",
        "kw": ["forest", "лес", "cabin", "домик", "избушк", "хижин", "утро", "morning",
               "сосн", "pine", "ель", "тишин", "quiet", "wooden"],
        "palette": {"base": "#0e1410", "primary": "#4ade80", "accent": "#fbbf24", "warm": "#f97316"},
        "mood": "photorealistic forest cabin in morning fog, quiet cinematic mood",
        "light": "soft green-tinted fog light, warm window glow cutting through, god rays between pines",
        "perspective": [0.55, 0.5],
        "elements": [
            {"id": "pines-far", "bbox": [0.0, 0.05, 1.0, 0.5], "z": "far", "color": "#1d2b22",
             "role": "layered pine forest silhouettes fading into fog"},
            {"id": "fog", "bbox": [0.0, 0.42, 1.0, 0.68], "z": "mid", "color": "#a7c4b5", "glow": True,
             "role": "low morning fog glowing in the light"},
            {"id": "cabin", "bbox": [0.35, 0.42, 0.68, 0.72], "z": "mid", "color": "#6b4423",
             "role": "wooden log cabin with a stone chimney"},
            {"id": "window-light", "bbox": [0.44, 0.52, 0.52, 0.62], "z": "mid", "color": "#fbbf24", "glow": True,
             "role": "warm lit window of the cabin"},
            {"id": "path", "bbox": [0.3, 0.72, 0.85, 1.0], "z": "near", "color": "#2a2f26",
             "role": "dirt path leading to the cabin through grass"},
        ],
    },
]


def _scale(el: dict, w: int, h: int) -> dict:
    x0, y0, x1, y1 = el["bbox"]
    return {**el, "bbox": [round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)]}


def pick_pattern(task: str) -> dict:
    t = task.lower()
    best, best_score = None, 0
    for p in PATTERNS:
        s = sum(1 for k in p["kw"] if k in t)
        if s > best_score:
            best, best_score = p, s
    if best is None:  # ничего не узнали — детерминированный поворот по хэшу
        best = PATTERNS[int(hashlib.sha256(t.encode()).hexdigest(), 16) % len(PATTERNS)]
    return best


def compose_layout_patterns(task: str, size=(1280, 720)) -> dict:
    """ЯРУС 0: мозг без сети — архетип + задание в mood + канва."""
    w, h = size
    p = pick_pattern(task)
    return {
        "canvas": {"w": w, "h": h},
        "style": {"mood": f"{p['mood']} — task: {task.strip()}"[:300],
                  "palette": p["palette"], "light": p["light"], "perspective": p["perspective"]},
        "elements": [_scale(e, w, h) for e in p["elements"]],
    }, p["name"]


def llm_compose(task: str, size=(1280, 720)) -> dict | None:
    """ЯРУС 1: мозг LLM (z-ai chat) → JSON-лейаут. Любая ошибка → None (не застреваем)."""
    if not shutil.which("z-ai"):
        return None
    w, h = size
    system = (
        "Ты — составитель лейаутов сцен для генерации изображений (метод layout→render). "
        f"Верни ТОЛЬКО JSON без пояснений: canvas {w}x{h}; style(mood, palette base/primary/"
        "accent/warm, light, perspective [x,y] 0..1); elements 5..8 шт с id, bbox [x0,y0,x1,y1] "
        "в ПИКСЕЛЯХ на канве, z far|mid|near, role на английском, color hex, glow bool. "
        "Сцена должна читаться: глубина far→near, главный субъект near в центре."
    )
    try:
        out = Path("/tmp/revo_llm.json")
        r = subprocess.run(["z-ai", "chat", "-p", f"Задание: {task}. Канва {w}x{h}.",
                            "-s", system, "-o", str(out)],
                           capture_output=True, text=True, timeout=180)
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        content = (data.get("choices", [{}])[0].get("message", {}).get("content")
                   or data.get("content") or data.get("output") or r.stdout or "")
        s, e = content.find("{"), content.rfind("}")
        if s < 0 or e <= s:
            return None
        lay = json.loads(content[s:e + 1])
        if validate(lay):
            return None
        return lay
    except Exception:
        return None


def compose_layout(task: str, size=(1280, 720)):
    """Мозг: LLM если доступен, иначе паттерны. Всегда возвращает (layout, brain)."""
    lay = llm_compose(task, size)
    if lay is not None:
        return lay, "llm"
    return compose_layout_patterns(task, size)


# ============================================================ КРИТИК-ГЛАЗА ===
# Свой, детерминированный: сверяет рендер с лейаутом по зонам. Без внешних API.

def _rgb(img, box):
    region = img.crop(box)
    px = list(region.getdata())
    n = len(px) or 1
    mr = sum(p[0] for p in px) / n
    mg = sum(p[1] for p in px) / n
    mb = sum(p[2] for p in px) / n
    ml = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in px) / n
    var = sum((0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] - ml) ** 2 for p in px) / n
    return (mr, mg, mb), ml, var ** 0.5


def zone_pos(el, w, h) -> str:
    x0, y0, x1, y1 = el["bbox"]
    cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    vy = "upper part" if cy < 0.30 else ("lower part" if cy > 0.72 else "middle ground")
    vx = "on the left" if cx < 0.34 else ("on the right" if cx > 0.66 else "in the center")
    return f"{vx}, {vy}"


def analyze_zones(render_path: str, lay: dict) -> dict:
    img = Image.open(render_path).convert("RGB")
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    if img.size != (w, h):
        img = img.resize((w, h))
    base = hex2rgb(lay.get("style", {}).get("palette", {}).get("base", "#0b0f1a"))
    ml_all = _rgb(img, (0, 0, w, h))[1]
    zones, verdicts, wsum, ssum = [], [], 0.0, 0.0
    for el in lay["elements"]:
        (mr, mg, mb), ml, std = _rgb(img, tuple(el["bbox"]))
        col = hex2rgb(el.get("color", "#888888"))
        # ожидаемый цвет зоны: элемент, растворённый в фоне по глубине (как в offline-рендере
        # и как выцветают дальние планы в реальной сцене)
        expected = mix(base, col, 1 - Z_FADE[el.get("z", "mid")] * 0.8)
        dist = ((mr - expected[0]) ** 2 + (mg - expected[1]) ** 2 + (mb - expected[2]) ** 2) ** 0.5
        color_prox = 1 - min(1.0, dist / 140)
        detail = min(1.0, std / 36)
        z = 0.55 * color_prox + 0.45 * detail
        if el.get("glow") and ml > ml_all + 8:
            z = min(1.0, z + 0.15)
        zw = Z_WEIGHT[el.get("z", "mid")]
        wsum += zw
        ssum += z * zw
        zones.append({"id": el["id"], "score": round(z, 3), "mean_rgb": [round(mr), round(mg), round(mb)],
                      "detail_std": round(std, 1)})
        if z < 0.55:
            verdicts.append(
                f"СЛАБО: '{el['id']}' ({el.get('z')}): ждали {el.get('color')} {zone_pos(el, w, h)}, "
                f"получили rgb({round(mr)},{round(mg)},{round(mb)}), детальность {std:.0f}")
    overall = 10 * ssum / max(wsum, 1e-6)
    if not verdicts and overall < PASS_SCORE:
        for z in sorted(zones, key=lambda x: x["score"])[:2]:
            verdicts.append(f"ХВОСТ: '{z['id']}' (score {z['score']}) — усилить контраст/насыщенность")
    return {"overall": round(overall, 2), "zones": zones, "verdicts": verdicts,
            "pass": overall >= PASS_SCORE}


def refine_layout(lay: dict, report: dict) -> dict:
    """Offline-рефайн: слабым зонам добавляем свечение и насыщенность к primary."""
    pal = lay["style"]["palette"]
    primary = hex2rgb(pal.get("primary", "#3b82f6"))
    weak = {v.split("'")[1] for v in report["verdicts"] if "'" in v}
    for el in lay["elements"]:
        if el["id"] in weak:
            col = hex2rgb(el.get("color", pal.get("accent", "#14b8a6")))
            el["color"] = "#%02x%02x%02x" % mix(col, primary, 0.35)
            el["glow"] = True
    return lay


def refine_prompt_text(base_prompt: str, lay: dict, report: dict) -> str:
    """Zai-рефайн: в промпт добавляются жёсткие указания по слабым зонам."""
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    fixes = []
    for el in lay["elements"]:
        if any(f"'{el['id']}'" in v for v in report["verdicts"]):
            fixes.append(f"IMPORTANT: {el['role']} clearly visible {zone_pos(el, w, h)}, "
                         f"dominant color {el.get('color')}, high contrast, not empty")
    return base_prompt + " " + " ".join(fixes) if fixes else base_prompt


# ================================================================= РЕНДЕР ====
# (skeleton / compile_prompt / render_offline / render_zai / merge — как в v1)

def font(size: int, paths=None):
    for p in (paths or FONTS):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def hex2rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t: float):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def load_layout(path: str) -> dict:
    lay = json.loads(Path(path).read_text(encoding="utf-8"))
    errs = validate(lay)
    if errs:
        sys.exit("REVO: layout невалиден:\n  - " + "\n  - ".join(errs))
    return lay


def validate(lay: dict) -> list:
    errs = []
    cv = lay.get("canvas") or {}
    w, h = cv.get("w"), cv.get("h")
    if not (isinstance(w, int) and isinstance(h, int) and 64 <= w <= 4096 and 64 <= h <= 4096):
        errs.append("canvas.w/h — целые 64..4096")
    els = lay.get("elements")
    if not isinstance(els, list) or not els:
        errs.append("elements — непустой список")
        return errs
    for i, el in enumerate(els):
        tag = f"elements[{i}]({el.get('id', '?')})"
        bb = el.get("bbox")
        if not (isinstance(bb, list) and len(bb) == 4 and all(isinstance(v, int) for v in bb)):
            errs.append(f"{tag}: bbox = [x0,y0,x1,y1] целыми")
            continue
        x0, y0, x1, y1 = bb
        if not (0 <= x0 < x1 <= (w or 10**9) and 0 <= y0 < y1 <= (h or 10**9)):
            errs.append(f"{tag}: bbox вне полотна {w}x{h}")
        if el.get("z", "mid") not in Z_ORDER:
            errs.append(f"{tag}: z ∈ {{far, mid, near}}")
        if not el.get("id"):
            errs.append(f"{tag}: нет id")
    return errs


def draw_skeleton(lay: dict, out: str) -> str:
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    base = hex2rgb(lay.get("style", {}).get("palette", {}).get("base", "#0b0f1a"))
    img = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(img, "RGBA")
    f_id, f_role, f_head = font(20), font(15, MONO), font(22)
    d.rectangle([0, 0, w, 44], fill=(0, 0, 0, 170))
    d.text((18, 10), "REVO · STEP 1 · LAYOUT (structure)", font=f_head, fill=(240, 244, 255))
    for el in sorted(lay["elements"], key=lambda e: Z_ORDER[e.get("z", "mid")]):
        x0, y0, x1, y1 = el["bbox"]
        col = hex2rgb(el.get("color", lay.get("style", {}).get("palette", {}).get("accent", "#14b8a6")))
        d.rounded_rectangle([x0, y0, x1, y1], radius=10, outline=col + (230,), width=3, fill=col + (26,))
        d.text((x0 + 8, y0 + 6), el["id"], font=f_id, fill=(255, 255, 255))
        role = el.get("role", "")
        if len(role) > 46:
            role = role[:45] + "…"
        d.text((x0 + 8, y0 + 30), role, font=f_role, fill=col + (235,))
    vp = lay.get("style", {}).get("perspective")
    if vp:
        px, py = int(vp[0] * w), int(vp[1] * h)
        d.line([px - 18, py, px + 18, py], fill=(255, 200, 60), width=2)
        d.line([px, py - 18, px, py + 18], fill=(255, 200, 60), width=2)
        d.text((px + 22, py - 8), "vanishing point", font=f_role, fill=(255, 200, 60))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


def compile_prompt(lay: dict) -> str:
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    st = lay.get("style", {})
    pal = st.get("palette", {})
    parts = []
    for el in sorted(lay["elements"], key=lambda e: Z_ORDER[e.get("z", "mid")], reverse=True):
        parts.append(f"{el['role']} {zone_pos(el, w, h)}")
    out = [st.get("mood", "photorealistic, cinematic")]
    out.append("; ".join(parts))
    if pal:
        cols = ", ".join(f"{k} {v}" for k, v in pal.items() if k != "base" and isinstance(v, str))
        if cols:
            out.append("palette: " + cols)
    if st.get("light"):
        out.append("lighting: " + st["light"])
    if st.get("perspective"):
        out.append(f"one-point perspective, vanishing point at "
                   f"({st['perspective'][0] * w:.0f}, {st['perspective'][1] * h:.0f})")
    out.append(f"composition locked to layout, aspect {w}:{h}, high quality, detailed")
    return ". ".join(p.rstrip(".") for p in out) + "."


def render_offline(lay: dict, out: str) -> str:
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    pal = lay.get("style", {}).get("palette", {})
    base = hex2rgb(pal.get("base", "#0b0f1a"))
    img = Image.new("RGB", (w, h), base)
    dr = ImageDraw.Draw(img)
    top = mix(base, (255, 255, 255), 0.06)
    for y in range(h):
        dr.line([(0, y), (w, y)], fill=mix(base, top, max(0.0, 1 - y / (h * 0.6))))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for el in sorted(lay["elements"], key=lambda e: Z_ORDER[e.get("z", "mid")]):
        x0, y0, x1, y1 = el["bbox"]
        col = hex2rgb(el.get("color", pal.get("accent", "#14b8a6")))
        faded = mix(base, col, 1 - Z_FADE[el.get("z", "mid")])
        if el.get("glow"):
            gd.ellipse([x0 - 40, y0 - 40, x1 + 40, y1 + 40], fill=col + (70,))
        dr.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=faded)
        dr.rounded_rectangle([x0, y0, x1, min(y1, y0 + 6)], radius=3, fill=mix(faded, (255, 255, 255), 0.25))
    glow = glow.filter(ImageFilter.GaussianBlur(34))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    vign = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vign).ellipse([-w * 0.25, -h * 0.35, w * 1.25, h * 1.35], fill=255)
    img = Image.composite(img, Image.new("RGB", (w, h), mix(base, (0, 0, 0), 0.5)),
                          vign.filter(ImageFilter.GaussianBlur(120)))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


def render_zai(lay: dict, out: str, size: str = "1344x768", prompt: str | None = None) -> str:
    if not shutil.which("z-ai"):
        sys.exit("REVO: z-ai CLI не найден в PATH — используй --backend offline")
    prompt = prompt or compile_prompt(lay)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["z-ai", "image", "-p", prompt, "-o", out, "-s", size],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not Path(out).exists():
        sys.exit(f"REVO: z-ai упал:\n{r.stdout}\n{r.stderr}")
    return out


def merge(a_path: str, b_path: str, out: str,
          labels=("STEP 1 · LAYOUT", "STEP 2 · RENDER")) -> str:
    a, b = Image.open(a_path).convert("RGB"), Image.open(b_path).convert("RGB")
    ph = 560
    a = a.resize((round(a.width * ph / a.height), ph))
    b = b.resize((round(b.width * ph / b.height), ph))
    gap, head, foot = 16, 56, 34
    W = a.width + gap + b.width
    img = Image.new("RGB", (W, head + ph + foot), (10, 13, 24))
    d = ImageDraw.Draw(img)
    d.text((16, 13), "REVO · pipeline: layout → render", font=font(26), fill=(240, 244, 255))
    img.paste(a, (0, head))
    img.paste(b, (a.width + gap, head))
    d.text((16, head + ph + 8), labels[0], font=font(16, MONO), fill=(120, 170, 255))
    d.text((a.width + gap + 16, head + ph + 8), labels[1], font=font(16, MONO), fill=(60, 220, 190))
    d.rectangle([a.width, head, a.width + gap - 1, head + ph], fill=(40, 46, 70))
    d.text((a.width + gap // 2 - 4, head + ph // 2 - 10), "→", font=font(22), fill=(255, 200, 60))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


def run_pipeline(lay_path: str, outdir: str, backend: str | None = None,
                 size: str = "1344x768") -> dict:
    """v1-режим одним вызовом: layout → skeleton + prompt + render + merge + отчёт."""
    lay = load_layout(lay_path)
    od = Path(outdir)
    od.mkdir(parents=True, exist_ok=True)
    stem = Path(lay_path).stem
    sk = draw_skeleton(lay, str(od / f"{stem}__skeleton.png"))
    pr = compile_prompt(lay)
    (od / f"{stem}__prompt.txt").write_text(pr, encoding="utf-8")
    be = backend or detect_backend()
    if be == "zai":
        rp = render_zai(lay, str(od / f"{stem}__render_zai.png"), size, pr)
    else:
        rp = render_offline(lay, str(od / f"{stem}__render_offline.png"))
    mg = merge(sk, rp, str(od / f"{stem}__pipeline_{be}.png"))
    rep = {
        "date_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "layout": lay_path, "backend": be, "canvas": [lay["canvas"]["w"], lay["canvas"]["h"]],
        "layout_elements": len(lay["elements"]),
        "skeleton": sk, "prompt": pr, "render": rp, "pipeline": mg,
    }
    (od / f"{stem}__run.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return rep


# ================================================================ АВТОПИЛОТ ==

def detect_backend() -> str:
    return "zai" if shutil.which("z-ai") else "offline"


def next_task(queue: str | None, invent: bool = True) -> str:
    if queue and Path(queue).exists():
        lines = [l.strip() for l in Path(queue).read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        if lines:
            doy = dt.datetime.now(dt.timezone.utc).timetuple().tm_yday
            return lines[doy % len(lines)]
    if invent:
        p = PATTERNS[int(hashlib.sha256(str(dt.date.today()).encode()).hexdigest(), 16) % len(PATTERNS)]
        return f"autonomous scene: {p['mood']} ({p['name']} pattern)"
    sys.exit("REVO: очередь пуста, а invent запрещён")


def run_auto(task: str, outdir: str, backend: str | None = None,
             attempts: int = 3, size: str = "1344x768") -> dict:
    """Полный автономный цикл: мозг → рендер → критика → рефайн → лучший кадр."""
    backend = backend or detect_backend()
    lay, brain = compose_layout(task)
    od = Path(outdir)
    od.mkdir(parents=True, exist_ok=True)
    skeleton = draw_skeleton(lay, str(od / "skeleton.png"))
    base_prompt = compile_prompt(lay)
    (od / "task.txt").write_text(task, encoding="utf-8")
    (od / "layout.json").write_text(json.dumps(lay, ensure_ascii=False, indent=2), encoding="utf-8")

    history, best = [], None
    prompt = base_prompt
    for i in range(1, attempts + 1):
        if backend == "zai":
            rp = str(od / f"render_a{i}.png")
            render_zai(lay, rp, size, prompt)
            Image.open(rp).convert("RGB").resize((lay["canvas"]["w"], lay["canvas"]["h"])).save(rp, optimize=True)
        else:
            lay = refine_layout(lay, history[-1]["critic"]) if history else lay
            rp = render_offline(lay, str(od / f"render_a{i}.png"))
        crit = analyze_zones(rp, lay)
        (od / f"critic_a{i}.json").write_text(json.dumps(crit, ensure_ascii=False, indent=2), encoding="utf-8")
        history.append({"attempt": i, "render": rp, "critic": crit})
        if best is None or crit["overall"] > best["critic"]["overall"]:
            best = history[-1]
        if crit["pass"]:
            break
        prompt = refine_prompt_text(base_prompt, lay, crit) if backend == "zai" else base_prompt

    pipeline = merge(skeleton, best["render"], str(od / "pipeline_best.png"))
    (od / "layout.json").write_text(json.dumps(lay, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "date_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "task": task, "brain": brain, "backend": backend, "attempts": len(history),
        "best_attempt": best["attempt"], "score": best["critic"]["overall"],
        "pass": best["critic"]["pass"], "verdicts": best["critic"]["verdicts"],
        "pipeline": pipeline, "skeleton": skeleton, "layout_elements": len(lay["elements"]),
    }
    (od / "__run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def autolog(outdir: str, outfile: str = "AUTO-LOG.md") -> str:
    """Журнал автономных прогонов — генерируется из __run.json, числа не руками."""
    runs = []
    for rj in sorted(Path(outdir).glob("*/__run.json")):
        try:
            runs.append(json.loads(rj.read_text(encoding="utf-8")))
        except Exception:
            continue
    lines = ["# AUTO-LOG — автономные прогоны REVO", "",
             "> Генерируется `revo.py autolog` из `demo/auto/*/__run.json`. Числа — никогда руками.", "",
             "| Дата (UTC) | Задание | Мозг | Бэкенд | Попыток | Score | Вердикт | Кадр |",
             "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        d = Path(r["pipeline"]).as_posix()
        lines.append(f"| {r['date_utc'][:16]} | {r['task'][:70]} | {r['brain']} | {r['backend']} "
                     f"| {r['attempts']} | **{r['score']}** | {'✅ pass' if r['pass'] else '🔶 хвост'} "
                     f"| [{Path(d).name}]({d}) |")
    txt = "\n".join(lines) + "\n"
    Path(outfile).write_text(txt, encoding="utf-8")
    return outfile


# ================================================================= САМОТЕСТ ==

def selftest() -> int:
    import tempfile
    ok = 0
    total = 12  # 10 проверок ядра (M0/M1) + 2 проверки M2 (детерминизм N-02, сервер F-14)
    tmp = tempfile.mkdtemp(prefix="revo-selftest-")
    good = {"canvas": {"w": 640, "h": 360},
            "style": {"palette": {"base": "#0b0f1a", "accent": "#14b8a6"}},
            "elements": [{"id": "a", "bbox": [10, 10, 300, 200], "z": "far", "role": "test zone",
                          "color": "#14b8a6"}]}
    errs = validate(good)
    print(("PASS" if not errs else "FAIL"), "1 validate(good)")
    ok += not errs
    bad = {"canvas": {"w": 10, "h": 10}, "elements": [{"id": "x", "bbox": [5, 5, 99, 9]}]}
    print(("PASS" if validate(bad) else "FAIL"), "2 validate(bad) ловит")
    ok += bool(validate(bad))
    lay1, brain1 = compose_layout("ночной дата-центр на 10000 GPU")
    print(("PASS" if brain1 == "llm" or pick_pattern("ночной дата-центр на 10000 GPU")["name"] == "datacenter"
           and not validate(lay1) else "FAIL"), f"3 compose(datacenter task) → {brain1}")
    ok += not validate(lay1) and pick_pattern("ночной дата-центр на 10000 GPU")["name"] == "datacenter"
    lay2, brain2 = compose_layout("караоке клуб неон", (640, 360))
    print(("PASS" if pick_pattern("караоке клуб неон")["name"] == "karaoke" and not validate(lay2)
           else "FAIL"), "4 compose(karaoke task)")
    ok += pick_pattern("караоке клуб неон")["name"] == "karaoke" and not validate(lay2)
    pr = compile_prompt(lay1)
    print(("PASS" if "palette" in pr and len(pr) > 100 else "FAIL"), "5 compile_prompt")
    ok += "palette" in pr and len(pr) > 100
    rp = render_offline(lay2, f"{tmp}/r.png")
    im = Image.open(rp)
    print(("PASS" if im.size == (640, 360) else "FAIL"), "6 render_offline размер")
    ok += im.size == (640, 360)
    crit = analyze_zones(rp, lay2)
    print(("PASS" if 0 <= crit["overall"] <= 10 and crit["zones"] else "FAIL"),
          f"7 критик в диапазоне ({crit['overall']})")
    ok += 0 <= crit["overall"] <= 10 and bool(crit["zones"])
    mg = merge(rp, rp, f"{tmp}/m.png")
    print(("PASS" if Path(mg).exists() else "FAIL"), "8 merge")
    ok += Path(mg).exists()
    out = autolog(f"{tmp}/empty-nope", f"{tmp}/AUTO-LOG.md")
    print(("PASS" if Path(out).exists() else "FAIL"), "9 autolog рождается из пустоты")
    ok += Path(out).exists()
    glp = Path(tmp) / "pipe_layout.json"
    glp.write_text(json.dumps(good), encoding="utf-8")
    rep = run_pipeline(str(glp), f"{tmp}/pipe", "offline")
    pipe_ok = (all(Path(rep[k]).exists() for k in ("skeleton", "render", "pipeline"))
               and isinstance(rep["prompt"], str) and len(rep["prompt"]) > 50)
    print(("PASS" if pipe_ok else "FAIL"), "10 pipeline v1-режим одним вызовом")
    ok += pipe_ok

    # 11. детерминизм offline-рендера (N-02): одинаковый layout → байт-в-байт одинаковый PNG
    ok11, note11 = False, ""
    try:
        dc = json.loads((Path(__file__).resolve().parent / "layouts" / "datacenter.json")
                        .read_text(encoding="utf-8"))
        a = render_offline(dc, f"{tmp}/det_a.png")
        b = render_offline(dc, f"{tmp}/det_b.png")
        ha = hashlib.sha256(Path(a).read_bytes()).hexdigest()
        hb = hashlib.sha256(Path(b).read_bytes()).hexdigest()
        ok11 = bool(ha) and ha == hb
    except Exception as e:
        note11 = f"{type(e).__name__}: {e}"
    print(("PASS" if ok11 else "FAIL"),
          f"11 детерминизм offline-рендера (SHA256 двух рендеров datacenter){' — ' + note11 if note11 else ''}")
    ok += ok11

    # 12. сервер M2 (F-14): revo_server.py в subprocess на свободном порту, /health → 200 и "ok": true
    ok12, note12 = False, ""
    srv = Path(__file__).resolve().parent / "revo_server.py"
    if not srv.exists():
        note12 = "revo_server.py не найден рядом с revo.py"
    else:
        import socket
        import time
        import urllib.request
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        proc = subprocess.Popen([sys.executable, str(srv), "--port", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                if proc.poll() is not None:
                    note12 = f"сервер завершился раньше времени (code {proc.returncode})"
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                        if r.status == 200 and json.loads(r.read().decode("utf-8")).get("ok") is True:
                            ok12 = True
                            break
                except Exception:
                    time.sleep(0.2)
            else:
                note12 = "сервер не ответил /health 200 за 20 с"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    print(("PASS" if ok12 else "FAIL"),
          f"12 revo_server.py /health → 200 и ok:true{' — ' + note12 if note12 else ''}")
    ok += ok12

    print(f"--- selftest {ok}/{total}")
    return 0 if ok == total else 1


# ==================================================================== CLI ====

def main(argv=None):
    ap = argparse.ArgumentParser(prog="revo",
                                 description="REVO v2: автономный layout → render (метод Reve)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate"); p.add_argument("layout")
    p = sub.add_parser("skeleton"); p.add_argument("layout"); p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("prompt"); p.add_argument("layout"); p.add_argument("-o", "--out")
    p = sub.add_parser("compose"); p.add_argument("task"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--size", default="1280x720")
    p = sub.add_parser("render"); p.add_argument("layout"); p.add_argument("-o", "--out", required=True)
    p.add_argument("-b", "--backend", choices=["offline", "zai"], default=None)
    p.add_argument("-s", "--size", default="1344x768")
    p = sub.add_parser("pipeline"); p.add_argument("layout"); p.add_argument("--outdir", required=True)
    p.add_argument("-b", "--backend", choices=["offline", "zai"], default=None)
    p.add_argument("-s", "--size", default="1344x768")
    p = sub.add_parser("merge"); p.add_argument("a"); p.add_argument("b"); p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("critic"); p.add_argument("render"); p.add_argument("layout")
    p = sub.add_parser("patterns"); p.add_argument("-t", "--task", default="")
    p = sub.add_parser("auto"); p.add_argument("task", nargs="?")
    p.add_argument("--queue", default=None)
    p.add_argument("--outdir", default=None)
    p.add_argument("-b", "--backend", choices=["offline", "zai"], default=None)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("-s", "--size", default="1344x768")
    p = sub.add_parser("autolog"); p.add_argument("outdir", nargs="?", default="demo/auto")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        errs = validate(json.loads(Path(args.layout).read_text(encoding="utf-8")))
        print("VALID ✓" if not errs else "INVALID:\n- " + "\n- ".join(errs))
        sys.exit(0 if not errs else 2)
    if args.cmd == "skeleton":
        print("saved:", draw_skeleton(load_layout(args.layout), args.out))
    elif args.cmd == "prompt":
        t = compile_prompt(load_layout(args.layout))
        if args.out:
            Path(args.out).write_text(t, encoding="utf-8")
            print("saved:", args.out)
        else:
            print(t)
    elif args.cmd == "compose":
        size = tuple(int(v) for v in args.size.split("x"))
        lay, brain = compose_layout(args.task, size)
        Path(args.out).write_text(json.dumps(lay, ensure_ascii=False, indent=2), encoding="utf-8")
        errs = validate(lay)
        print(f"brain={brain} → {args.out} {'VALID ✓' if not errs else 'INVALID: ' + '; '.join(errs)}")
        sys.exit(0 if not errs else 2)
    elif args.cmd == "render":
        lay_r = load_layout(args.layout)
        if (args.backend or detect_backend()) == "zai":
            print("saved:", render_zai(lay_r, args.out, args.size))
        else:
            print("saved:", render_offline(lay_r, args.out))
    elif args.cmd == "pipeline":
        print(json.dumps(run_pipeline(args.layout, args.outdir, args.backend, args.size),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "merge":
        print("saved:", merge(args.a, args.b, args.out))
    elif args.cmd == "critic":
        print(json.dumps(analyze_zones(args.render, load_layout(args.layout)),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "patterns":
        if args.task:
            p = pick_pattern(args.task)
            print(f"→ {p['name']} (канва-шаблон, {len(p['elements'])} зон)")
        else:
            for p in PATTERNS:
                print(f"{p['name']:12s} {len(p['elements'])} зон · kw: {', '.join(p['kw'][:5])}…")
    elif args.cmd == "auto":
        task = args.task or next_task(args.queue)
        outdir = args.outdir or f"demo/auto/{dt.date.today().isoformat()}"
        rep = run_auto(task, outdir, args.backend, args.attempts, args.size)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    elif args.cmd == "autolog":
        print("saved:", autolog(args.outdir))
    elif args.cmd == "selftest":
        sys.exit(selftest())


if __name__ == "__main__":
    main()
