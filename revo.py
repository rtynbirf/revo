#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REVO — REnder-Via-layout-Orchestrator.
Прототип метода Reve («The Layout Bet»): сначала LAYOUT (структура сцены),
потом RENDER (картинка, собранная строго из структуры).

Пайплайн:  layout.json → [1] скелет PNG → [2] рендер-промпт → [3] картинка → [4] склейка

Бэкенды рендера:
  offline  — детерминированный векторный рендер из layout (без сети, без API)
  zai      — генерация через z-ai CLI (`z-ai image -p ... -o ...`)

Зависимости: Python 3.9+, Pillow. Всё остальное — опционально.
Дом прототипа: https://github.com/rtynbirf/revo · вселенная rtynbirf · 2026-11-09
"""
from __future__ import annotations

import argparse
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
Z_FADE = {"far": 0.62, "mid": 0.30, "near": 0.0}  # насколько растворять цвет в фоне

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
MONO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


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


# ------------------------------------------------------------------ layout --

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


# ---------------------------------------------------------------- skeleton --

def draw_skeleton(lay: dict, out: str) -> str:
    """ЭТАП 1: структурный скелет — рамки зон, подписи, точка схода."""
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
        d.rounded_rectangle([x0, y0, x1, y1], radius=10, outline=col + (230,), width=3,
                            fill=col + (26,))
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


# ------------------------------------------------------------------ prompt --

def compile_prompt(lay: dict) -> str:
    """ЭТАП 2: детерминированный рендер-промпт ИЗ структуры (никакой лотереи)."""
    w, h = lay["canvas"]["w"], lay["canvas"]["h"]
    st = lay.get("style", {})
    pal = st.get("palette", {})
    parts = []

    for el in sorted(lay["elements"], key=lambda e: Z_ORDER[e.get("z", "mid")], reverse=True):
        x0, y0, x1, y1 = el["bbox"]
        cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
        if cy < 0.30:
            vy = "upper part"
        elif cy > 0.72:
            vy = "lower part, foreground"
        else:
            vy = "middle ground"
        if cx < 0.34:
            vx = "on the left"
        elif cx > 0.66:
            vx = "on the right"
        else:
            vx = "in the center"
        parts.append(f"{el['role']} {vx}, {vy}")

    out = [st.get("mood", "photorealistic, cinematic")]
    out.append("; ".join(parts))
    if pal:
        cols = ", ".join(f"{k} {v}" for k, v in pal.items()
                         if k != "base" and isinstance(v, str))
        if cols:
            out.append("palette: " + cols)
    if st.get("light"):
        out.append("lighting: " + st["light"])
    if st.get("perspective"):
        out.append(f"one-point perspective, vanishing point at "
                   f"({st['perspective'][0] * w:.0f}, {st['perspective'][1] * h:.0f})")
    out.append(f"composition locked to layout, aspect {w}:{h}, high quality, detailed")
    return ". ".join(p.rstrip(".") for p in out) + "."


# ------------------------------------------------------------------ render --

def render_offline(lay: dict, out: str) -> str:
    """ЭТАП 3 (offline): детерминированный векторный рендер из той же структуры."""
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
        dr.rounded_rectangle([x0, y0, x1, min(y1, y0 + 6)], radius=3,
                             fill=mix(faded, (255, 255, 255), 0.25))
    glow = glow.filter(ImageFilter.GaussianBlur(34))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    vign = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vign).ellipse([-w * 0.25, -h * 0.35, w * 1.25, h * 1.35], fill=255)
    img = Image.composite(img, Image.new("RGB", (w, h), mix(base, (0, 0, 0), 0.5)),
                          vign.filter(ImageFilter.GaussianBlur(120)))

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


def render_zai(lay: dict, out: str, size: str = "1440x768") -> str:
    """ЭТАП 3 (zai): генерация строго по скомпилированному промпту."""
    if not shutil.which("z-ai"):
        sys.exit("REVO: z-ai CLI не найден в PATH — используй --backend offline")
    prompt = compile_prompt(lay)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["z-ai", "image", "-p", prompt, "-o", out, "-s", size],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not Path(out).exists():
        sys.exit(f"REVO: z-ai упал:\n{r.stdout}\n{r.stderr}")
    return out


# ------------------------------------------------------------------- merge --

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
    f_h, f_f = font(26), font(16, MONO)
    d.text((16, 13), "REVO · pipeline: layout → render", font=f_h, fill=(240, 244, 255))
    img.paste(a, (0, head))
    img.paste(b, (a.width + gap, head))
    d.text((16, head + ph + 8), labels[0], font=f_f, fill=(120, 170, 255))
    d.text((a.width + gap + 16, head + ph + 8), labels[1], font=f_f, fill=(60, 220, 190))
    d.rectangle([a.width, head, a.width + gap - 1, head + ph], fill=(40, 46, 70))
    d.text((a.width + gap // 2 - 4, head + ph // 2 - 10), "→", font=font(22), fill=(255, 200, 60))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


# ---------------------------------------------------------------- pipeline --

def run_pipeline(lay_path: str, outdir: str, backend: str = "offline",
                 size: str = "1440x768") -> dict:
    lay = load_layout(lay_path)
    od = Path(outdir)
    od.mkdir(parents=True, exist_ok=True)
    name = lay_path.rsplit("/", 1)[-1].replace(".json", "")
    skeleton = draw_skeleton(lay, str(od / f"{name}__skeleton.png"))
    prompt = compile_prompt(lay)
    (od / f"{name}__prompt.txt").write_text(prompt, encoding="utf-8")
    if backend == "zai":
        render = render_zai(lay, str(od / f"{name}__render_zai.png"), size)
        rimg = Image.open(render).convert("RGB").resize((lay["canvas"]["w"], lay["canvas"]["h"]))
        rimg.save(render, optimize=True)
    else:
        render = render_offline(lay, str(od / f"{name}__render_offline.png"))
    merged = merge(skeleton, render, str(od / f"{name}__pipeline_{backend}.png"))
    report = {"layout": str(lay_path), "backend": backend, "skeleton": skeleton,
              "render": render, "pipeline": merged, "prompt_chars": len(prompt)}
    (od / f"{name}__run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="revo", description="REVO: layout → render (метод Reve)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate"); p.add_argument("layout")
    p = sub.add_parser("skeleton"); p.add_argument("layout"); p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("prompt"); p.add_argument("layout"); p.add_argument("-o", "--out")
    p = sub.add_parser("render"); p.add_argument("layout"); p.add_argument("-o", "--out", required=True)
    p.add_argument("-b", "--backend", choices=["offline", "zai"], default="offline")
    p.add_argument("-s", "--size", default="1440x768")
    p = sub.add_parser("merge"); p.add_argument("a"); p.add_argument("b"); p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("pipeline"); p.add_argument("layout")
    p.add_argument("--outdir", default="demo")
    p.add_argument("-b", "--backend", choices=["offline", "zai"], default="offline")
    p.add_argument("-s", "--size", default="1440x768")
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
    elif args.cmd == "render":
        fn = render_zai if args.backend == "zai" else render_offline
        print("saved:", fn(load_layout(args.layout), args.out, args.size))
    elif args.cmd == "merge":
        print("saved:", merge(args.a, args.b, args.out))
    elif args.cmd == "pipeline":
        print(json.dumps(run_pipeline(args.layout, args.outdir, args.backend, args.size),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
