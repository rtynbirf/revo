#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обложка LABS №26 для репо revo: арт z-ai + типографика PIL, 1280x640."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SRC = "cover_art_raw.png"
OUT = ".github/social-preview.png"
W, H = 1280, 640

art = Image.open(SRC).convert("RGB")
# кроп 2:1 по центру
target_ratio = W / H
aw, ah = art.size
if aw / ah > target_ratio:
    nw = int(ah * target_ratio)
    art = art.crop(((aw - nw) // 2, 0, (aw - nw) // 2 + nw, ah))
else:
    nh = int(aw / target_ratio)
    art = art.crop((0, (ah - nh) // 2, aw, (ah - nh) // 2 + nh))
art = art.resize((W, H))

img = art.copy()
d = ImageDraw.Draw(img, "RGBA")

FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FM = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
f_word = ImageFont.truetype(FM, 26)
f_title = ImageFont.truetype(FB, 150)
f_chip = ImageFont.truetype(FB, 30)
f_tag = ImageFont.truetype(FB, 25)
f_url = ImageFont.truetype(FM, 22)

# затемнение слева под текст
grad = Image.new("L", (W, H), 0)
gd = ImageDraw.Draw(grad)
for x in range(W):
    gd.line([(x, 0), (x, H)], fill=max(0, 200 - int(200 * x / (W * 0.72))))
img = Image.composite(Image.new("RGB", (W, H), (8, 11, 22)), img, grad)
d = ImageDraw.Draw(img, "RGBA")

# лёгкая нижняя тень для URL
bottom = Image.new("L", (W, H), 0)
bd = ImageDraw.Draw(bottom)
for y in range(H - 140, H):
    bd.line([(0, y), (W, y)], fill=int(170 * (y - (H - 140)) / 140))
img = Image.composite(Image.new("RGB", (W, H), (8, 11, 22)), img, bottom)
d = ImageDraw.Draw(img, "RGBA")

# wordmark
d.text((56, 46), "RTYNBIRF LABS", font=f_word, fill=(150, 190, 255, 255))
d.line([(56, 88), (336, 88)], fill=(47, 111, 237, 220), width=3)

# заголовок
d.text((48, 108), "REVO", font=f_title, fill=(242, 246, 255, 255))

# чип LAYOUT → RENDER
chip = "LAYOUT → RENDER"
tw = d.textlength(chip, font=f_chip)
cx, cy = 56, 292
d.rounded_rectangle([cx, cy, cx + tw + 44, cy + 56], radius=14,
                    fill=(47, 111, 237, 60), outline=(96, 165, 250, 255), width=2)
d.text((cx + 22, cy + 11), chip, font=f_chip, fill=(147, 197, 253, 255))

# тэглайн
d.text((56, 372), "СНАЧАЛА СТРУКТУРА — ПОТОМ КАРТИНКА", font=f_tag, fill=(226, 232, 240, 255))
d.text((56, 408), "прототип метода Reve · layout.json → скелет → рендер", font=f_tag,
       fill=(148, 163, 184, 255))

# url
d.text((56, H - 54), "github.com/rtynbirf/revo", font=f_url, fill=(96, 165, 250, 255))
d.text((W - 330, H - 54), "№26 · 2026-11-09", font=f_url, fill=(120, 134, 160, 255))

img.save(OUT, optimize=True)
print("saved", OUT, img.size)
