/* REVO · M3 — SPA без сборки: vanilla JS + <canvas>. Работает офлайн, в т.ч. с file://.
   Контракт API: docs/API.md §2 · формат данных: docs/FORMAT.md (layout.json v1) ·
   экраны: docs/RESTORE-APP.md §4 · ТЗ: SPEC.md §5 блок D (F-18..F-21).
   Полный цикл — через revo_server.py; без сервера — честный демо-режим (канвас-эмуляция). */
"use strict";

(function () {

  // ================= утилиты =================
  const $ = (s) => document.getElementById(s);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const Z_LIST = ["far", "mid", "near"];
  const Z_FADE = { far: 0.62, mid: 0.30, near: 0.00 }; // растворение по глубине (FORMAT.md §4)
  const Z_STYLE = {
    far:  { stroke: "#4ea8ff" },
    mid:  { stroke: "#b18cff" },
    near: { stroke: "#ffb054" },
  };
  const FILL_ALPHA = { far: 0.10, mid: 0.16, near: 0.22 };
  const PAL_DEFAULT = { base: "#0b0f1a", primary: "#2f6fed", accent: "#14b8a6", warm: "#f59e0b" };
  const MIN_SIZE = 4;   // минимальная сторона зоны при ресайзе, px полотна
  const DRAW_MIN = 6;   // минимальная сторона новой зоны при рисовании, px полотна
  const PAD = 16;       // запас при подгонке масштаба канваса

  function isHexColor(v) { return typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v); }
  function hexToRgb(hex) {
    const h = isHexColor(hex) ? hex : "#888888";
    return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  }
  function hexA(hex, a) { const [r, g, b] = hexToRgb(hex); return "rgba(" + r + "," + g + "," + b + "," + a + ")"; }
  function mixHex(a, b, t) {
    const A = hexToRgb(a), B = hexToRgb(b);
    const c = A.map((v, i) => Math.round(v + (B[i] - v) * t));
    return "#" + c.map((v) => clamp(v, 0, 255).toString(16).padStart(2, "0")).join("");
  }
  function safeColor(v, fallback) { return isHexColor(v) ? v : (fallback || "#888888"); }

  // ================= состояние =================
  const state = {
    layout: null,            // layout.json v1: {canvas, style, elements}
    sel: -1,                 // индекс выбранной зоны
    scale: 1,                // масштаб отображения (координаты всегда в пикселях полотна)
    lastZ: "mid",            // глубина новых зон
    drag: null,              // {mode:'move'|'resize'|'draw', handle, orig, sx, sy, cur}
    server: { ok: false, base: null, info: null },
    gallery: { skeleton: null, render: null }, // {src, source}
    galleryUrls: new Map(),  // blob: URL по слотам (для revoke)
    history: loadHistory(),
    cycleRunning: false,
  };

  // ================= нормализация layout.json (парсер) =================
  // Терпимо принимает файл/ответ API, приводит к канону docs/FORMAT.md v1,
  // сохраняет неизвестные поля (вперёд-совместимость: валидатор их пропускает).
  function normalizeLayout(raw) {
    const warn = [];
    const lay = { canvas: { w: 1280, h: 720 }, style: {}, elements: [] };
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      warn.push("документ не объект — создан пустой лейаут 1280×720");
      return { lay, warn };
    }
    // неизвестные корневые поля — вперёд-совместимость
    for (const k of Object.keys(raw)) {
      if (k !== "canvas" && k !== "style" && k !== "elements") lay[k] = raw[k];
    }
    // canvas.w/h — целые 64..4096 (FORMAT.md §2)
    const c = raw.canvas || {};
    const cw = Math.round(Number(c.w)), ch = Math.round(Number(c.h));
    if (Number.isInteger(cw) && Number.isInteger(ch)) {
      lay.canvas.w = clamp(cw, 64, 4096);
      lay.canvas.h = clamp(ch, 64, 4096);
      if (lay.canvas.w !== cw || lay.canvas.h !== ch) warn.push("canvas приведён к 64..4096");
    } else {
      warn.push("canvas.w/h не целые — подставлено 1280×720");
    }
    const W = lay.canvas.w, H = lay.canvas.h;

    // style (FORMAT.md §5): mood, palette, light, perspective [x,y] в долях 0..1
    const s = raw.style || {};
    lay.style.mood = typeof s.mood === "string" ? s.mood : "";
    lay.style.light = typeof s.light === "string" ? s.light : "";
    const pal = (s.palette && typeof s.palette === "object" && !Array.isArray(s.palette)) ? s.palette : {};
    lay.style.palette = Object.assign({}, pal);
    if (!isHexColor(lay.style.palette.base)) lay.style.palette.base = PAL_DEFAULT.base;
    let p = Array.isArray(s.perspective) ? s.perspective : [0.5, 0.47];
    p = [clamp(Number(p[0]) || 0, 0, 1), clamp(Number(p[1]) || 0, 0, 1)];
    lay.style.perspective = p;

    // elements (FORMAT.md §3): id / bbox / z / role / color / glow (+ доп. поля)
    const src = Array.isArray(raw.elements) ? raw.elements : [];
    if (!Array.isArray(raw.elements)) warn.push("elements не список — создан пустой список");
    src.forEach((e) => {
      if (!e || typeof e !== "object" || Array.isArray(e)) { warn.push("пропущен не-объект в elements"); return; }
      const out = Object.assign({}, e); // доп. поля v1.1+ сохраняем
      // id
      if (typeof e.id === "string" && e.id.trim()) out.id = e.id.trim();
      else { out.id = "e" + (lay.elements.length + 1); warn.push("элемент без id — выдан " + out.id); }
      // bbox [x0,y0,x1,y1] — целые в пикселях полотна
      let b = Array.isArray(e.bbox) ? e.bbox.map(Number) : [];
      if (b.length === 4 && b.every((v) => Number.isFinite(v))) {
        b = b.map((v) => Math.round(v));
        let [x0, y0, x1, y1] = b;
        if (x0 > x1) { const t = x0; x0 = x1; x1 = t; }
        if (y0 > y1) { const t = y0; y0 = y1; y1 = t; }
        const nb = [clamp(x0, 0, W), clamp(y0, 0, H), clamp(x1, 0, W), clamp(y1, 0, H)];
        if (nb[2] - nb[0] < 1 || nb[3] - nb[1] < 1) {
          warn.push("элемент «" + out.id + "»: вырожденный bbox — расширен до 1×1");
          nb[2] = Math.min(W, nb[0] + 1); nb[3] = Math.min(H, nb[1] + 1);
        }
        if (nb.some((v, i) => v !== b[i])) warn.push("элемент «" + out.id + "»: bbox обрезан по полотну");
        out.bbox = nb;
      } else {
        out.bbox = [Math.round(W * 0.25), Math.round(H * 0.25), Math.round(W * 0.5), Math.round(H * 0.5)];
        warn.push("элемент «" + out.id + "»: битый bbox — подставлен центральный");
      }
      // z: far | mid | near (иное → mid)
      if (!Z_LIST.includes(e.z)) { if (e.z !== undefined && e.z !== "mid") warn.push("элемент «" + out.id + "»: z → mid"); out.z = "mid"; }
      else out.z = e.z;
      // role: строка EN как есть (для компилятора промпта)
      out.role = typeof e.role === "string" ? e.role : "";
      // color: hex; без него рендер возьмёт palette.accent
      out.color = isHexColor(e.color) ? e.color : "";
      // glow: bool
      out.glow = !!e.glow;
      lay.elements.push(out);
    });
    return { lay, warn };
  }

  // ================= локальная валидация (зеркало docs/FORMAT.md §8) =================
  // Канон — POST /api/validate; эта — честный фолбэк для file://.
  function validateLocal(lay) {
    const errs = [];
    const c = (lay && lay.canvas) || {};
    const W = c.w, H = c.h;
    const intOk = (v) => Number.isInteger(v);
    if (!intOk(W) || !intOk(H) || W < 64 || W > 4096 || H < 64 || H > 4096) {
      errs.push("canvas.w/h — целые 64..4096");
    }
    const els = lay && Array.isArray(lay.elements) ? lay.elements : null;
    if (!els || els.length === 0) errs.push("elements — непустой список");
    if (els) {
      els.forEach((e, i) => {
        const label = e && typeof e.id === "string" && e.id ? e.id : "#" + i;
        if (!e || typeof e.id !== "string" || !e.id.trim()) errs.push("elements[" + i + "]: нет id");
        const b = e && e.bbox;
        if (!Array.isArray(b) || b.length !== 4 || !b.every(intOk)) {
          errs.push("elements[" + i + "](" + label + "): bbox = [x0,y0,x1,y1] целыми");
        } else if (intOk(W) && intOk(H)) {
          if (b[0] < 0 || b[1] < 0 || b[2] > W || b[3] > H) {
            errs.push("elements[" + i + "](" + label + "): bbox вне полотна " + W + "x" + H);
          }
          if (b[0] >= b[2] || b[1] >= b[3]) {
            errs.push("elements[" + i + "](" + label + "): требуется x0<x1 и y0<y1");
          }
        }
        if (e && e.z !== undefined && !Z_LIST.includes(e.z)) {
          errs.push("elements[" + i + "](" + label + "): z ∈ {far, mid, near}");
        }
      });
    }
    return errs;
  }

  // ================= вспомогательное для лейаута =================
  function drawOrder(lay) { // индексы зон в порядке отрисовки far → near (FORMAT.md §4)
    return lay.elements.map((e, i) => ({ e, i }))
      .sort((a, b) => Z_LIST.indexOf(a.e.z) - Z_LIST.indexOf(b.e.z) || a.i - b.i)
      .map((o) => o.i);
  }
  function autoId() {
    const used = new Set(state.layout.elements.map((e) => e.id));
    for (let i = 1; i < 100000; i++) { const id = "e" + i; if (!used.has(id)) return id; }
    return "e" + Date.now();
  }
  function currentLayout() { return state.layout; }

  // ================= локальный компилятор промпта (эскиз по FORMAT.md §5–6) =================
  function positionWords(e, canvas) {
    const cx = (e.bbox[0] + e.bbox[2]) / 2 / canvas.w;
    const cy = (e.bbox[1] + e.bbox[3]) / 2 / canvas.h;
    const px = cx < 0.34 ? "on the left" : cx > 0.66 ? "on the right" : "in the center";
    const py = cy < 0.30 ? "upper part" : cy > 0.72 ? "lower part" : "middle ground";
    return px + ", " + py;
  }
  function localPrompt(lay) {
    const st = lay.style || {};
    const pal = st.palette || {};
    const parts = [];
    if (st.mood) parts.push(st.mood.replace(/\s+$/, "") + ".");
    const palStr = Object.keys(pal).map((k) => k + " " + pal[k]).join(", ");
    if (palStr) parts.push("palette: " + palStr + ".");
    if (st.light) parts.push("lighting: " + st.light + ".");
    if (st.perspective) {
      parts.push("one-point perspective, vanishing point at (" + st.perspective[0] + ", " + st.perspective[1] + ").");
    }
    const byZ = { near: [], mid: [], far: [] };
    for (const e of lay.elements) (byZ[e.z] || byZ.mid).push(e);
    for (const z of ["near", "mid", "far"]) { // ближние — первыми словами (FORMAT.md §4)
      for (const e of byZ[z]) parts.push(((e.role || e.id) + " " + positionWords(e, lay.canvas) + ".").trim());
    }
    return parts.join(" ");
  }

  // ================= API-слой (docs/API.md §2) =================
  function fetchTimeout(url, opts, ms) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms || 6000);
    return fetch(url, Object.assign({}, opts || {}, { signal: ctl.signal }))
      .finally(() => clearTimeout(t));
  }
  async function apiJson(method, path, body, ms) {
    if (!state.server.ok || !state.server.base) throw new Error("нет сервера (демо-режим)");
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetchTimeout(state.server.base + path, opts, ms);
    const text = await r.text();
    let j = null;
    try { j = JSON.parse(text); } catch (e) { /* не JSON */ }
    if (!r.ok) {
      const detail = j ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j.error || j)) : ("HTTP " + r.status);
      const err = new Error(detail);
      err.status = r.status; err.json = j;
      throw err;
    }
    return j;
  }
  async function apiBlob(method, path, body, ms) {
    if (!state.server.ok || !state.server.base) throw new Error("нет сервера (демо-режим)");
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetchTimeout(state.server.base + path, opts, ms);
    if (!r.ok) {
      let msg = "HTTP " + r.status;
      try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j.error || j); } catch (e) { /* бинарник не пришёл */ }
      throw new Error(msg);
    }
    return await r.blob();
  }

  // автоопределение базы API: тот же origin → localhost:8400 → 127.0.0.1:8400
  async function detectServer() {
    const cands = [];
    if (location.protocol === "http:" || location.protocol === "https:") cands.push(location.origin);
    cands.push("http://localhost:8400", "http://127.0.0.1:8400");
    for (const base of cands) {
      try {
        const r = await fetchTimeout(base + "/health", {}, 2500);
        if (r.ok) {
          const j = await r.json();
          if (j && j.ok === true) {
            state.server = { ok: true, base, info: j };
            setServerUI(true, base, j);
            setDemoUI(false);
            toast("Сервер подключён: " + base, "ok");
            return;
          }
        }
      } catch (e) { /* нет сервера — пробуем следующий адрес */ }
    }
    state.server = { ok: false, base: null, info: null };
    setServerUI(false, null, null);
    setDemoUI(true);
  }

  function setServerUI(ok, base, info) {
    const dot = $("server-dot"), lab = $("server-label");
    if (ok) {
      dot.className = "dot ok";
      lab.textContent = "сервер: " + base + (info && info.backend ? " · backend " + info.backend : "");
      lab.title = "GET /health ok · api=" + (info && info.api != null ? info.api : "?") + " · version=" + (info && info.version ? info.version : "?");
    } else {
      dot.className = "dot off";
      lab.textContent = "нет сервера — демо-режим";
      lab.title = "Поднимите: python3 revo_server.py (порт 8400)";
    }
  }

  function setDemoUI(demo) {
    $("demo-banner").hidden = !demo;
    ["btn-critic", "btn-patterns", "btn-apply"].forEach((id) => {
      const b = $(id);
      if (!b) return;
      b.disabled = demo;
      b.title = demo ? "Требуется сервер: python3 revo_server.py" : "";
    });
    if (demo) {
      $("nl-note").textContent = "Демо-режим: compose (мозг) и критик работают только с сервером — python3 revo_server.py";
    }
  }

  // ================= тост =================
  let toastTimer = null;
  function toast(msg, type) {
    const t = $("toast");
    t.textContent = msg;
    t.className = type || "";
    t.style.display = "block";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.style.display = "none"; }, 4200);
  }

  // ================= канвас-редактор =================
  let cv = null, ctx = null; // инициализируются в boot() (модуль не должен требовать DOM)

  function renderCanvas() {
    const lay = state.layout;
    if (!lay || !ctx) return;
    const wrap = $("canvas-wrap");
    const W = lay.canvas.w, H = lay.canvas.h;
    const availW = Math.max(80, wrap.clientWidth - PAD * 2);
    const availH = Math.max(80, wrap.clientHeight - PAD * 2);
    state.scale = clamp(Math.min(availW / W, availH / H), 0.05, 2);
    const s = state.scale;
    const dpr = window.devicePixelRatio || 1;
    cv.style.width = (W * s) + "px";
    cv.style.height = (H * s) + "px";
    cv.width = Math.max(1, Math.round(W * s * dpr));
    cv.height = Math.max(1, Math.round(H * s * dpr));
    ctx.setTransform(s * dpr, 0, 0, s * dpr, 0, 0);

    // полотно — цвет palette.base
    ctx.fillStyle = safeColor(lay.style.palette && lay.style.palette.base, PAL_DEFAULT.base);
    ctx.fillRect(0, 0, W, H);

    // зоны far → near
    for (const idx of drawOrder(lay)) drawZoneEditor(lay.elements[idx], idx, s);

    // точка схода (perspective, FORMAT.md §5)
    drawVanishing(lay, s);

    // рамка выбранной зоны + ручки
    if (state.sel >= 0 && state.sel < lay.elements.length) drawSelection(lay.elements[state.sel].bbox, s);

    // превью рисуемой зоны
    if (state.drag && state.drag.mode === "draw" && state.drag.cur) {
      const d = state.drag;
      const x0 = Math.min(d.sx, d.cur.x), y0 = Math.min(d.sy, d.cur.y);
      const w = Math.abs(d.cur.x - d.sx), h = Math.abs(d.cur.y - d.sy);
      ctx.save();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5 / s;
      ctx.setLineDash([6 / s, 4 / s]);
      ctx.strokeRect(x0, y0, w, h);
      ctx.setLineDash([]);
      ctx.font = (11 / s) + "px system-ui, Arial, sans-serif";
      ctx.fillStyle = "#ffffff";
      ctx.fillText(Math.round(w) + "×" + Math.round(h), x0 + 4 / s, y0 - 6 / s);
      ctx.restore();
    }
  }

  function drawZoneEditor(e, idx, s) {
    const [x0, y0, x1, y1] = e.bbox;
    const w = x1 - x0, h = y1 - y0;
    const zs = Z_STYLE[e.z] || Z_STYLE.mid;
    const col = e.color || zs.stroke;
    ctx.save();
    if (e.glow) { ctx.shadowColor = col; ctx.shadowBlur = 16 / s; }
    ctx.fillStyle = hexA(col, FILL_ALPHA[e.z] != null ? FILL_ALPHA[e.z] : 0.16);
    ctx.fillRect(x0, y0, w, h);
    ctx.restore();
    ctx.lineWidth = 2 / s;
    ctx.strokeStyle = zs.stroke;
    ctx.strokeRect(x0, y0, w, h);

    // подписи: id · реальный размер, ниже — role (обрезан)
    const fs = 12 / s, fs2 = 10.5 / s;
    ctx.textBaseline = "top";
    ctx.textAlign = "left";
    const label = e.id + " · " + w + "×" + h;
    ctx.font = "600 " + fs + "px system-ui, Arial, sans-serif";
    const tw = ctx.measureText(label).width;
    let lx = x0 + 3 / s, ly = y0 + 3 / s;
    if (lx + tw + 6 / s > state.layout.canvas.w) lx = Math.max(2 / s, state.layout.canvas.w - tw - 8 / s);
    ctx.fillStyle = "rgba(5,8,14,0.78)";
    ctx.fillRect(lx - 2 / s, ly - 1 / s, tw + 5 / s, fs * 1.3);
    ctx.fillStyle = "#e6e9f0";
    ctx.fillText(label, lx, ly + 1 / s);
    if (e.role) {
      ctx.font = fs2 + "px system-ui, Arial, sans-serif";
      const role = e.role.length > 38 ? e.role.slice(0, 37) + "…" : e.role;
      const tw2 = ctx.measureText(role).width;
      ctx.fillStyle = "rgba(5,8,14,0.72)";
      ctx.fillRect(lx - 2 / s, ly + fs * 1.35, tw2 + 5 / s, fs2 * 1.35);
      ctx.fillStyle = hexA(zs.stroke, 0.95);
      ctx.fillText(role, lx, ly + fs * 1.35 + 1 / s);
    }
    if (e.glow) { // маркер свечения
      ctx.font = fs2 + "px system-ui, Arial, sans-serif";
      ctx.fillStyle = col;
      ctx.fillText("✦ glow", x0 + 3 / s, y1 - fs2 * 1.5);
    }
  }

  function drawVanishing(lay, s) {
    const p = (lay.style && lay.style.perspective) || [0.5, 0.47];
    const W = lay.canvas.w, H = lay.canvas.h;
    const px = clamp(p[0], 0, 1) * W, py = clamp(p[1], 0, 1) * H;
    const L = Math.max(12, W * 0.02);
    ctx.save();
    ctx.strokeStyle = "#ff5d73";
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = 2 / s;
    ctx.beginPath();
    ctx.moveTo(px - L, py); ctx.lineTo(px + L, py);
    ctx.moveTo(px, py - L); ctx.lineTo(px, py + L);
    ctx.stroke();
    ctx.font = (10.5 / s) + "px system-ui, Arial, sans-serif";
    ctx.fillStyle = "#ff5d73";
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText("точка схода", px + L * 0.55, py + L * 0.3);
    ctx.restore();
  }

  function handlesOf(b) {
    const [x0, y0, x1, y1] = b;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return [["nw", x0, y0], ["n", cx, y0], ["ne", x1, y0], ["e", x1, cy], ["se", x1, y1], ["s", cx, y1], ["sw", x0, y1], ["w", x0, cy]];
  }
  function drawSelection(b, s) {
    const [x0, y0, x1, y1] = b;
    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.2 / s;
    ctx.setLineDash([6 / s, 4 / s]);
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    ctx.setLineDash([]);
    const hs = 9 / s;
    for (const [, hx, hy] of handlesOf(b)) {
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = "#0b0e14";
      ctx.lineWidth = 1.5 / s;
      ctx.beginPath();
      ctx.rect(hx - hs / 2, hy - hs / 2, hs, hs);
      ctx.fill(); ctx.stroke();
    }
    ctx.restore();
  }

  // ---------- мышь ----------
  function canvasPos(ev) {
    const r = cv.getBoundingClientRect();
    return { x: (ev.clientX - r.left) / state.scale, y: (ev.clientY - r.top) / state.scale };
  }
  function hitHandle(p) {
    if (state.sel < 0) return null;
    const b = state.layout.elements[state.sel].bbox;
    const tol = 9 / state.scale;
    for (const [name, hx, hy] of handlesOf(b)) {
      if (Math.abs(p.x - hx) <= tol && Math.abs(p.y - hy) <= tol) return name;
    }
    return null;
  }
  function hitElement(p) {
    const order = drawOrder(state.layout);
    for (let k = order.length - 1; k >= 0; k--) { // ближние — сверху
      const idx = order[k];
      const [x0, y0, x1, y1] = state.layout.elements[idx].bbox;
      if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1) return idx;
    }
    return -1;
  }

  const CURSORS = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };

  function onDown(ev) {
    if (ev.button !== 0 || !state.layout) return;
    ev.preventDefault();
    const p = canvasPos(ev);
    const lay = state.layout;
    if (ev.shiftKey) { // Shift+клик — поставить точку схода
      lay.style.perspective = [clamp(p.x / lay.canvas.w, 0, 1), clamp(p.y / lay.canvas.h, 0, 1)];
      fillSceneInspector();
      renderCanvas();
      toast("Точка схода: " + lay.style.perspective.map((v) => v.toFixed(3)).join(", "), "");
      return;
    }
    const h = hitHandle(p);
    if (h) {
      state.drag = { mode: "resize", handle: h, orig: lay.elements[state.sel].bbox.slice(), sx: p.x, sy: p.y };
      return;
    }
    const idx = hitElement(p);
    if (idx >= 0) {
      state.sel = idx;
      state.lastZ = lay.elements[idx].z;
      state.drag = { mode: "move", orig: lay.elements[idx].bbox.slice(), sx: p.x, sy: p.y };
      fillZoneInspector();
      renderCanvas();
    } else {
      state.drag = { mode: "draw", sx: p.x, sy: p.y, cur: null };
      renderCanvas();
    }
  }

  function onMove(ev) {
    if (state.drag) {
      const p = canvasPos(ev);
      const lay = state.layout;
      const W = lay.canvas.w, H = lay.canvas.h;
      const d = state.drag;
      const dx = p.x - d.sx, dy = p.y - d.sy;
      if (d.mode === "move") {
        const [ox0, oy0, ox1, oy1] = d.orig;
        const w = ox1 - ox0, h = oy1 - oy0;
        const nx0 = clamp(Math.round(ox0 + dx), 0, W - w);
        const ny0 = clamp(Math.round(oy0 + dy), 0, H - h);
        setBbox(state.sel, [nx0, ny0, nx0 + w, ny0 + h]);
      } else if (d.mode === "resize") {
        const o = d.orig;
        let x0 = o[0], y0 = o[1], x1 = o[2], y1 = o[3];
        const hd = d.handle;
        if (hd.includes("e")) x1 = clamp(Math.round(o[2] + dx), o[0] + MIN_SIZE, W);
        if (hd.includes("w")) x0 = clamp(Math.round(o[0] + dx), 0, o[2] - MIN_SIZE);
        if (hd.includes("s")) y1 = clamp(Math.round(o[3] + dy), o[1] + MIN_SIZE, H);
        if (hd.includes("n")) y0 = clamp(Math.round(o[1] + dy), 0, o[3] - MIN_SIZE);
        setBbox(state.sel, [x0, y0, x1, y1]);
      } else if (d.mode === "draw") {
        d.cur = p;
      }
      renderCanvas();
      fillZoneInspector();
      return;
    }
    if (ev.target === cv) { // курсор-подсказка
      const p = canvasPos(ev);
      const h = hitHandle(p);
      cv.style.cursor = h ? CURSORS[h] : (hitElement(p) >= 0 ? "move" : "crosshair");
    }
  }

  function onUp(ev) {
    const d = state.drag;
    if (!d) return;
    state.drag = null;
    if (d.mode === "draw" && d.cur) {
      const x0 = Math.round(Math.min(d.sx, d.cur.x));
      const y0 = Math.round(Math.min(d.sy, d.cur.y));
      const w = Math.round(Math.abs(d.cur.x - d.sx));
      const h = Math.round(Math.abs(d.cur.y - d.sy));
      if (w >= DRAW_MIN && h >= DRAW_MIN) {
        state.layout.elements.push({
          id: autoId(), bbox: [x0, y0, x0 + w, y0 + h],
          z: state.lastZ, role: "", color: "", glow: false,
        });
        state.sel = state.layout.elements.length - 1;
        fillZoneInspector();
        toast("Новая зона " + state.layout.elements[state.sel].id + " (" + w + "×" + h + " px)", "ok");
      } else {
        const p = canvasPos(ev);
        state.sel = hitElement(p); // короткий клик — снять/сменить выбор
        fillZoneInspector();
      }
    }
    renderCanvas();
  }

  function setBbox(idx, b) {
    const lay = state.layout;
    if (idx < 0 || idx >= lay.elements.length) return;
    lay.elements[idx].bbox = b.map((v) => Math.round(clamp(v, 0, 1e9)));
    // жёсткая привязка к полотну
    const W = lay.canvas.w, H = lay.canvas.h;
    let [x0, y0, x1, y1] = lay.elements[idx].bbox;
    x0 = clamp(x0, 0, W); x1 = clamp(x1, 0, W); y0 = clamp(y0, 0, H); y1 = clamp(y1, 0, H);
    if (x1 - x0 < MIN_SIZE) x1 = clamp(x0 + MIN_SIZE, 0, W);
    if (y1 - y0 < MIN_SIZE) y1 = clamp(y0 + MIN_SIZE, 0, H);
    lay.elements[idx].bbox = [x0, y0, x1, y1];
  }

  function newZone() {
    const lay = state.layout;
    const W = lay.canvas.w, H = lay.canvas.h;
    const w = Math.round(W / 4), h = Math.round(H / 4);
    const off = (lay.elements.length * 24) % Math.max(1, Math.round(W / 6));
    const x0 = clamp(Math.round(W * 0.375) + off - w / 2, 0, W - w);
    const y0 = clamp(Math.round(H * 0.375) + off - h / 2, 0, H - h);
    lay.elements.push({ id: autoId(), bbox: [x0, y0, x0 + w, y0 + h], z: state.lastZ, role: "", color: "", glow: false });
    state.sel = lay.elements.length - 1;
    fillZoneInspector();
    renderCanvas();
    $("z-role").focus();
  }

  function deleteSelected() {
    if (state.sel < 0) return;
    const gone = state.layout.elements.splice(state.sel, 1)[0];
    state.sel = -1;
    fillZoneInspector();
    renderCanvas();
    toast("Зона удалена: " + (gone ? gone.id : ""), "");
  }

  function setZ(z) {
    if (state.sel < 0) return;
    state.layout.elements[state.sel].z = z;
    state.lastZ = z;
    fillZoneInspector();
    renderCanvas();
  }

  // ================= инспектор =================
  function fillZoneInspector() {
    const lay = state.layout;
    const fields = $("zone-fields");
    const els = lay ? lay.elements : [];
    $("z-count").textContent = "(" + els.length + " зон)";
    $("btn-del").disabled = state.sel < 0;
    if (state.sel < 0 || !els[state.sel]) {
      fields.disabled = true;
      $("z-real").textContent = "зона не выбрана — перетащите по канвасу";
      return;
    }
    fields.disabled = false;
    const e = els[state.sel];
    $("z-id").value = e.id;
    $("z-role").value = e.role;
    ["far", "mid", "near"].forEach((z) => {
      const b = $("z-" + z);
      b.className = (z === e.z ? "active " : "") + z;
    });
    $("z-color").value = safeColor(e.color, safeColor(lay.style.palette && lay.style.palette.accent, PAL_DEFAULT.accent));
    $("z-glow").checked = !!e.glow;
    const [x0, y0, x1, y1] = e.bbox;
    $("z-x0").value = x0; $("z-y0").value = y0; $("z-x1").value = x1; $("z-y1").value = y1;
    $("z-real").textContent = "bbox " + x0 + "," + y0 + " → " + x1 + "," + y1 + " · " + (x1 - x0) + "×" + (y1 - y0) +
      " px (полотно " + lay.canvas.w + "×" + lay.canvas.h + ")";
  }

  function fillSceneInspector() {
    const lay = state.layout;
    if (!lay) return;
    const st = lay.style;
    $("s-mood").value = st.mood || "";
    $("s-light").value = st.light || "";
    const pal = st.palette || {};
    ["base", "primary", "accent", "warm"].forEach((k) => {
      $("p-" + k).value = safeColor(pal[k], PAL_DEFAULT[k]);
    });
    const p = Array.isArray(st.perspective) ? st.perspective : [0.5, 0.47];
    $("s-px").value = (+p[0]).toFixed(2);
    $("s-py").value = (+p[1]).toFixed(2);
  }

  function fillToolbar() {
    const lay = state.layout;
    $("cv-w").value = lay.canvas.w;
    $("cv-h").value = lay.canvas.h;
  }

  function refreshAll() {
    fillToolbar();
    fillZoneInspector();
    fillSceneInspector();
    renderCanvas();
  }

  // ================= галерея кадра =================
  function setImg(slotId, src, caption) {
    const slot = $(slotId);
    const old = state.galleryUrls.get(slotId);
    if (old && old.startsWith("blob:")) { try { URL.revokeObjectURL(old); } catch (e) { /* уже отозван */ } }
    if (src.startsWith("blob:")) state.galleryUrls.set(slotId, src); else state.galleryUrls.delete(slotId);
    const body = slot.querySelector(".g-body");
    body.innerHTML = "";
    const img = document.createElement("img");
    img.src = src;
    img.alt = slot.querySelector(".g-head").textContent;
    img.title = "клик — скачать PNG";
    img.addEventListener("click", () => {
      const a = document.createElement("a");
      a.href = src;
      a.download = slotId.replace("g-", "") + ".png";
      a.click();
    });
    body.appendChild(img);
    slot.classList.add("filled");
    slot.querySelector(".g-caption").textContent = caption || "";
  }

  function setSlotText(slotId, text, caption) {
    const slot = $(slotId);
    const old = state.galleryUrls.get(slotId);
    if (old && old.startsWith("blob:")) { try { URL.revokeObjectURL(old); } catch (e) { /* уже отозван */ } }
    state.galleryUrls.delete(slotId);
    const body = slot.querySelector(".g-body");
    body.innerHTML = "";
    const pre = document.createElement("pre");
    pre.className = "g-pre";
    pre.textContent = String(text == null ? "" : text);
    body.appendChild(pre);
    slot.classList.add("filled");
    slot.querySelector(".g-caption").textContent = caption || "";
  }

  function storeGallery(key, src, source) { state.gallery[key] = { src, source }; }

  // ================= цикл (F-20): Скелет · Промпт · Рендер · Критик · Склейка =================
  async function doSkeleton() {
    const lay = currentLayout();
    if (state.server.ok) {
      try {
        const blob = await apiBlob("POST", "/api/skeleton", lay);
        const url = URL.createObjectURL(blob);
        setImg("g-skeleton", url, "POST /api/skeleton · сервер " + state.server.base + " · " + lay.canvas.w + "×" + lay.canvas.h);
        storeGallery("skeleton", url, "server");
        return;
      } catch (e) {
        toast("Скелет: сервер не ответил (" + e.message + ") — локальная эмуляция", "warn");
      }
    }
    const du = localSkeleton(lay);
    setImg("g-skeleton", du, "скелет · ЭМУЛЯЦИЯ (canvas) · локально по docs/FORMAT.md §2–5");
    storeGallery("skeleton", du, "local");
  }

  async function doPrompt() {
    const lay = currentLayout();
    if (state.server.ok) {
      try {
        const j = await apiJson("POST", "/api/prompt", lay);
        setSlotText("g-prompt", j && j.prompt ? j.prompt : "(пустой ответ)", "POST /api/prompt · детерминированный компилятор ядра");
        return;
      } catch (e) {
        toast("Промпт: сервер не ответил (" + e.message + ") — локальный эскиз", "warn");
      }
    }
    setSlotText("g-prompt", localPrompt(lay), "промпт · ЭСКИЗ локально по docs/FORMAT.md §5–6 (канонический — через сервер)");
  }

  async function doRender() {
    const lay = currentLayout();
    const backend = $("backend").value;
    if (state.server.ok) {
      try {
        const blob = await apiBlob("POST", "/api/render", { layout: lay, backend, size: [lay.canvas.w, lay.canvas.h] });
        const url = URL.createObjectURL(blob);
        setImg("g-render", url, "рендер · бэкенд " + backend + " · POST /api/render · " + lay.canvas.w + "×" + lay.canvas.h);
        storeGallery("render", url, "server");
        return;
      } catch (e) {
        toast("Рендер: сервер не ответил (" + e.message + ") — локальная эмуляция", "warn");
      }
    }
    const du = localRender(lay);
    setImg("g-render", du, "рендер · ЭМУЛЯЦИЯ: заливка зон с растворением far .62 / mid .30 / near 0 (полный рендер — через сервер)");
    storeGallery("render", du, "local");
  }

  async function doCritic() {
    if (!state.server.ok) {
      toast("Критик недоступен в демо-режиме — нужен сервер (python3 revo_server.py)", "warn");
      return;
    }
    const r = state.gallery.render;
    if (!r) { toast("Сначала сделайте «Рендер»", "warn"); return; }
    try {
      const b64 = await srcToBase64(r.src);
      const j = await apiJson("POST", "/api/critic", { layout: currentLayout(), render_png_base64: b64 });
      renderCriticReport(j);
    } catch (e) {
      toast("Критик: " + e.message, "err");
    }
  }

  function renderCriticReport(j) {
    const body = $("g-critic").querySelector(".g-body");
    body.innerHTML = "";
    const wrap = document.createElement("div");
    const top = document.createElement("div");
    top.className = "crit-top";
    const sc = document.createElement("span");
    sc.className = "crit-score";
    sc.textContent = (typeof j.overall === "number" ? j.overall.toFixed(1) : "—") + " / 10";
    const badge = document.createElement("span");
    badge.className = "badge " + (j.pass ? "ok" : "bad");
    badge.textContent = j.pass ? "PASS ≥ 7.5" : "FAIL < 7.5";
    top.appendChild(sc); top.appendChild(badge);
    wrap.appendChild(top);

    const zones = Array.isArray(j.zones) ? j.zones : [];
    if (zones.length) {
      const tbl = document.createElement("table");
      tbl.className = "crit-tbl";
      const thead = document.createElement("tr");
      ["зона", "score", "mean_rgb", "σ"].forEach((h) => {
        const th = document.createElement("th"); th.textContent = h; thead.appendChild(th);
      });
      tbl.appendChild(thead);
      zones.forEach((z) => {
        const tr = document.createElement("tr");
        [z.id, typeof z.score === "number" ? z.score.toFixed(2) : "—",
         Array.isArray(z.mean_rgb) ? z.mean_rgb.join(",") : "—",
         z.detail_std != null ? Number(z.detail_std).toFixed(1) : "—"].forEach((v) => {
          const td = document.createElement("td"); td.textContent = String(v); tr.appendChild(td);
        });
        tbl.appendChild(tr);
      });
      wrap.appendChild(tbl);
    }
    const verdicts = Array.isArray(j.verdicts) ? j.verdicts : [];
    if (verdicts.length) {
      const ul = document.createElement("ul");
      ul.className = "verdicts";
      verdicts.forEach((v) => { const li = document.createElement("li"); li.textContent = String(v); ul.appendChild(li); });
      wrap.appendChild(ul);
    }
    body.appendChild(wrap);
    $("g-critic").querySelector(".g-caption").textContent = "POST /api/critic · отчёт по зонам";
  }

  async function doMerge() {
    const lay = currentLayout();
    const backend = $("backend").value;
    let rep = null, imgDone = false;
    if (state.server.ok) {
      try {
        rep = await apiJson("POST", "/api/pipeline", { layout: lay, backend });
        const b64 = rep && (rep.pipeline_png_base64 || rep.pipeline_base64 ||
          (rep.images && rep.images.pipeline) || (rep.files && rep.files.pipeline_base64));
        if (b64) {
          setImg("g-merge", "data:image/png;base64," + b64, "склейка · PNG из POST /api/pipeline" + pipelineMeta(rep));
          imgDone = true;
        }
      } catch (e) {
        toast("Склейка: pipeline не ответил (" + e.message + ") — соберу локально", "warn");
      }
    }
    if (!imgDone) {
      const sk = state.gallery.skeleton, rn = state.gallery.render;
      if (sk && rn) {
        try {
          const du = await localMerge(sk.src, rn.src);
          setImg("g-merge", du, "локальная склейка «Шаг 1 · Лейаут → Шаг 2 · Рендер»" + (rep ? " · отчёт pipeline: " + pipelineMeta(rep) : ""));
          imgDone = true;
        } catch (e) {
          toast("Локальная склейка не удалась: " + e.message, "err");
        }
      }
      if (!imgDone && rep) {
        setSlotText("g-merge", JSON.stringify(rep, null, 2), "отчёт POST /api/pipeline (без PNG в ответе)");
        imgDone = true;
      }
      if (!imgDone) { toast("Для склейки нужны «Скелет» и «Рендер»", "warn"); return; }
    }
  }

  function pipelineMeta(rep) {
    const bits = [];
    if (rep && rep.backend) bits.push("backend=" + rep.backend);
    if (rep && Array.isArray(rep.canvas)) bits.push("canvas=" + rep.canvas.join("x"));
    if (rep && rep.layout_elements != null) bits.push("зон=" + rep.layout_elements);
    return bits.length ? " · " + bits.join(" · ") : "";
  }

  async function runCycle() { // Ctrl+Enter: скелет → промпт → рендер → критик → склейка
    if (state.cycleRunning) return;
    state.cycleRunning = true;
    try {
      toast("Прогон цикла: скелет → промпт → рендер → критик → склейка", "");
      await doSkeleton();
      await doPrompt();
      await doRender();
      await doCritic(); // в демо честно откажется
      await doMerge();
      toast("Цикл завершён", "ok");
    } finally {
      state.cycleRunning = false;
    }
  }

  // ================= NL-инструкция (F-21) =================
  function loadHistory() {
    try { return JSON.parse(localStorage.getItem("revo.m3.history.v1")) || []; }
    catch (e) { return []; }
  }
  function saveHistory() {
    try { localStorage.setItem("revo.m3.history.v1", JSON.stringify(state.history.slice(0, 30))); }
    catch (e) { /* file:// без localStorage — работаем в памяти */ }
  }
  function pushHistory(entry) {
    state.history.unshift(entry);
    state.history = state.history.slice(0, 30);
    saveHistory();
    renderHistory();
  }
  function renderHistory() {
    const ol = $("nl-history");
    ol.innerHTML = "";
    state.history.slice(0, 12).forEach((h) => {
      const li = document.createElement("li");
      const t = document.createElement("span"); t.className = "h-time"; t.textContent = h.time || "";
      const txt = document.createElement("span"); txt.textContent = h.text;
      li.appendChild(t); li.appendChild(txt);
      if (h.brain) {
        const b = document.createElement("span"); b.className = "h-brain"; b.textContent = "brain=" + h.brain;
        li.appendChild(b);
      }
      const badge = document.createElement("span");
      badge.className = "badge " + (h.status === "ok" ? "ok" : h.status === "error" ? "bad" : "dim2");
      badge.textContent = h.status === "ok" ? "ok" : h.status === "error" ? "ошибка" : "нет сервера";
      li.appendChild(badge);
      ol.appendChild(li);
    });
  }

  async function applyInstruction() {
    const task = $("nl-text").value.trim();
    if (!task) { toast("Введите инструкцию", "warn"); return; }
    const entry = { text: task.length > 90 ? task.slice(0, 89) + "…" : task, time: new Date().toLocaleTimeString("ru-RU"), brain: null, status: "ok" };
    if (!state.server.ok) {
      entry.status = "demo";
      pushHistory(entry);
      $("nl-note").textContent = "Демо-режим: compose (мозг) работает только с сервером — python3 revo_server.py";
      toast("compose недоступен без сервера", "warn");
      return;
    }
    try {
      const lay = currentLayout();
      const j = await apiJson("POST", "/api/compose", { task, size: [lay.canvas.w, lay.canvas.h] });
      const res = normalizeLayout(j && j.layout);
      state.layout = res.lay;
      state.sel = -1;
      entry.brain = (j && j.brain) || "?";
      entry.status = "ok";
      pushHistory(entry);
      refreshAll();
      $("nl-note").textContent = "brain=" + entry.brain + " · зон: " + res.lay.elements.length +
        (res.warn.length ? " · предупреждения: " + res.warn.join("; ") : "");
      toast("Лейаут пересобран мозгом «" + entry.brain + "»", "ok");
    } catch (e) {
      entry.status = "error";
      pushHistory(entry);
      $("nl-note").textContent = "Ошибка compose: " + e.message;
      toast("compose: " + e.message, "err");
    }
  }

  // ================= валидация / экспорт / импорт / паттерны =================
  async function doValidate() {
    const lay = currentLayout();
    if (state.server.ok) {
      try {
        const j = await apiJson("POST", "/api/validate", lay);
        showVerdict(!!(j && j.valid), (j && j.errors) || [], "POST /api/validate");
        return;
      } catch (e) {
        toast("Валидация: сервер не ответил (" + e.message + ") — локальная проверка", "warn");
      }
    }
    const errs = validateLocal(lay);
    showVerdict(errs.length === 0, errs, "локальная проверка (зеркало docs/FORMAT.md §8)");
  }

  function showVerdict(valid, errors, source) {
    const out = $("validate-out");
    out.innerHTML = "";
    const head = document.createElement("div");
    head.className = valid ? "v-ok" : "v-err";
    head.textContent = valid ? "VALID ✓" : "INVALID:";
    out.appendChild(head);
    (errors || []).forEach((msg) => {
      const d = document.createElement("div");
      d.className = "v-err";
      d.textContent = "— " + msg;
      out.appendChild(d);
    });
    const src = document.createElement("div");
    src.className = "dim";
    src.textContent = "(" + source + ")";
    out.appendChild(src);
    toast(valid ? "VALID ✓" : "INVALID: " + (errors || []).length + " ошибок", valid ? "ok" : "err");
  }

  function exportLayout() {
    const data = JSON.stringify(currentLayout(), null, 2) + "\n";
    const blob = new Blob([data], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "layout.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast("layout.json сохранён (" + currentLayout().elements.length + " зон)", "ok");
  }

  async function importLayout(file) {
    try {
      let text = "";
      if (file.text) text = await file.text();
      else text = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(String(r.result)); r.onerror = rej; r.readAsText(file);
      });
      const raw = JSON.parse(text);
      const res = normalizeLayout(raw);
      state.layout = res.lay;
      state.sel = -1;
      refreshAll();
      const errs = validateLocal(res.lay);
      toast("Импорт: " + res.lay.elements.length + " зон" +
        (res.warn.length ? " · предупреждения: " + res.warn.join("; ") : "") +
        (errs.length ? " · ЛОКАЛЬНО НЕВАЛИДНО: " + errs[0] : ""), errs.length ? "warn" : "ok");
    } catch (e) {
      toast("Импорт не удался: " + e.message, "err");
    }
  }

  async function doPatterns() {
    if (!state.server.ok) { toast("Паттерны доступны только с сервером", "warn"); return; }
    try {
      const j = await apiJson("GET", "/api/patterns");
      const lines = ((j && j.patterns) || []).map((p) => p.name + " (" + p.zones + " зон): " + (p.kw || []).join(", ")).join("\n");
      setSlotText("g-prompt", lines || "(пусто)", "GET /api/patterns · библиотека архетипов мозга");
    } catch (e) {
      toast("Паттерны: " + e.message, "err");
    }
  }

  // ================= локальная канвас-эмуляция (демо-режим file://) =================
  // НИКОГДА не выдаём эмуляцию за полный рендер: подпись «ЭМУЛЯЦИЯ» в каждой карточке.
  function localSkeleton(lay) {
    const W = lay.canvas.w, H = lay.canvas.h;
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const g = c.getContext("2d");
    g.fillStyle = safeColor(lay.style.palette && lay.style.palette.base, PAL_DEFAULT.base);
    g.fillRect(0, 0, W, H);
    const lw = Math.max(2, Math.round(W / 640));
    for (const idx of drawOrder(lay)) {
      const e = lay.elements[idx];
      const [x0, y0, x1, y1] = e.bbox;
      const zs = Z_STYLE[e.z] || Z_STYLE.mid;
      const col = e.color || zs.stroke;
      g.fillStyle = hexA(col, FILL_ALPHA[e.z] != null ? FILL_ALPHA[e.z] : 0.16);
      g.fillRect(x0, y0, x1 - x0, y1 - y0);
      g.lineWidth = lw;
      g.strokeStyle = zs.stroke;
      g.strokeRect(x0, y0, x1 - x0, y1 - y0);
      const fs = Math.max(13, Math.round(W / 60));
      g.font = "600 " + fs + "px system-ui, Arial, sans-serif";
      g.textBaseline = "top"; g.textAlign = "left";
      const label = e.id + " · " + (x1 - x0) + "×" + (y1 - y0) + (e.glow ? " · glow" : "");
      const tw = g.measureText(label).width;
      const pad = fs * 0.45;
      const lx = Math.min(x0 + pad, Math.max(pad, W - tw - pad * 2));
      const ly = Math.min(y0 + pad, Math.max(pad, H - fs * 2.4));
      g.fillStyle = "rgba(5,8,14,0.78)";
      g.fillRect(lx - pad * 0.5, ly - pad * 0.3, tw + pad, fs * 1.4);
      g.fillStyle = "#e6e9f0";
      g.fillText(label, lx, ly);
      if (e.role) {
        const fs2 = Math.round(fs * 0.78);
        g.font = fs2 + "px system-ui, Arial, sans-serif";
        const role = e.role.length > 44 ? e.role.slice(0, 43) + "…" : e.role;
        const tw2 = g.measureText(role).width;
        g.fillStyle = "rgba(5,8,14,0.72)";
        g.fillRect(lx - pad * 0.5, ly + fs * 1.5, tw2 + pad, fs2 * 1.5);
        g.fillStyle = hexA(zs.stroke, 0.95);
        g.fillText(role, lx, ly + fs * 1.6);
      }
    }
    // крест точки схода
    const p = (lay.style && lay.style.perspective) || [0.5, 0.47];
    const px = clamp(p[0], 0, 1) * W, py = clamp(p[1], 0, 1) * H;
    const L = Math.max(16, W * 0.025);
    g.strokeStyle = "#ff5d73";
    g.lineWidth = Math.max(2, Math.round(W / 500));
    g.beginPath();
    g.moveTo(px - L, py); g.lineTo(px + L, py);
    g.moveTo(px, py - L); g.lineTo(px, py + L);
    g.stroke();
    g.fillStyle = "#ff5d73";
    g.font = "600 " + Math.max(11, Math.round(W / 80)) + "px system-ui, Arial, sans-serif";
    g.textAlign = "left"; g.textBaseline = "top";
    g.fillText("точка схода", px + L * 0.6, py + L * 0.35);
    g.strokeStyle = "#232a36";
    g.lineWidth = 2;
    g.strokeRect(1, 1, W - 2, H - 2);
    return c.toDataURL("image/png");
  }

  function localRender(lay) {
    const W = lay.canvas.w, H = lay.canvas.h;
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const g = c.getContext("2d");
    const pal = (lay.style && lay.style.palette) || {};
    const base = safeColor(pal.base, PAL_DEFAULT.base);
    g.fillStyle = base;
    g.fillRect(0, 0, W, H);
    // мягкий свет от точки схода
    const p = (lay.style && lay.style.perspective) || [0.5, 0.47];
    const px = clamp(p[0], 0, 1) * W, py = clamp(p[1], 0, 1) * H;
    const rg = g.createRadialGradient(px, py, 10, px, py, Math.max(W, H) * 0.75);
    rg.addColorStop(0, hexA(safeColor(pal.primary, PAL_DEFAULT.primary), 0.16));
    rg.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = rg;
    g.fillRect(0, 0, W, H);
    // зоны far → near с растворением по глубине (Z_FADE из FORMAT.md §4)
    for (const idx of drawOrder(lay)) {
      const e = lay.elements[idx];
      const [x0, y0, x1, y1] = e.bbox;
      const fade = Z_FADE[e.z] != null ? Z_FADE[e.z] : 0.3;
      const col = safeColor(e.color, safeColor(pal.accent, PAL_DEFAULT.accent));
      const fill = mixHex(base, col, 1 - fade * 0.85);
      g.save();
      if (e.glow) {
        g.shadowColor = col;
        g.shadowBlur = Math.max(12, Math.min(W, H) * 0.05);
      }
      g.fillStyle = fill;
      g.fillRect(x0, y0, x1 - x0, y1 - y0);
      g.restore();
      if (e.glow) { // световое ядро свечения
        const gg = g.createRadialGradient((x0 + x1) / 2, (y0 + y1) / 2, 4, (x0 + x1) / 2, (y0 + y1) / 2, Math.max(x1 - x0, y1 - y0) * 0.7);
        gg.addColorStop(0, hexA(col, 0.28 * (1 - fade)));
        gg.addColorStop(1, "rgba(0,0,0,0)");
        g.fillStyle = gg;
        g.fillRect(x0 - W * 0.05, y0 - H * 0.05, (x1 - x0) + W * 0.1, (y1 - y0) + H * 0.1);
      }
    }
    // виньетка
    const vg = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.max(W, H) * 0.72);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,0.5)");
    g.fillStyle = vg;
    g.fillRect(0, 0, W, H);
    return c.toDataURL("image/png");
  }

  async function localMerge(skSrc, rnSrc) {
    const [a, b] = await Promise.all([loadImage(skSrc), loadImage(rnSrc)]);
    const W = a.naturalWidth || 1280, H = a.naturalHeight || 720;
    const gap = 28, top = 46;
    const c = document.createElement("canvas");
    c.width = W * 2 + gap;
    c.height = H + top + 10;
    const g = c.getContext("2d");
    g.fillStyle = "#05070d";
    g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = "#e6e9f0";
    g.font = "600 20px system-ui, Arial, sans-serif";
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText("ШАГ 1 · ЛЕЙАУТ (СКЕЛЕТ)", W / 2, top / 2);
    g.fillText("ШАГ 2 · РЕНДЕР", W + gap + W / 2, top / 2);
    g.drawImage(a, 0, top);
    g.drawImage(b, W + gap, top);
    g.strokeStyle = "#232a36";
    g.lineWidth = 2;
    g.strokeRect(1, top + 1, W - 2, H - 2);
    g.strokeRect(W + gap + 1, top + 1, W - 2, H - 2);
    return c.toDataURL("image/png");
  }

  function loadImage(src) {
    return new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error("изображение не загрузилось"));
      i.src = src;
    });
  }

  async function srcToBase64(src) {
    if (src.startsWith("data:")) return src.slice(src.indexOf(",") + 1);
    const blob = await (await fetch(src)).blob();
    return await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => { const s = String(r.result); res(s.slice(s.indexOf(",") + 1)); };
      r.onerror = rej;
      r.readAsDataURL(blob);
    });
  }

  // ================= привязка событий =================
  function bindEvents() {
    // хоткеи (RESTORE-APP §4): N — новая зона · Delete — удалить · 1/2/3 — far/mid/near · Ctrl+E · Ctrl+Enter
    window.addEventListener("keydown", (ev) => {
      const t = ev.target;
      const inField = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
      if ((ev.ctrlKey || ev.metaKey) && ev.code === "KeyE") { ev.preventDefault(); exportLayout(); return; }
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") { ev.preventDefault(); runCycle(); return; }
      if (inField || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      if (ev.code === "KeyN") { newZone(); }
      else if (ev.key === "Delete" || ev.key === "Backspace") { deleteSelected(); }
      else if (ev.code === "Digit1") { setZ("far"); }
      else if (ev.code === "Digit2") { setZ("mid"); }
      else if (ev.code === "Digit3") { setZ("near"); }
    });

    cv = $("cv");
    ctx = cv.getContext("2d");
    cv.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    cv.addEventListener("dblclick", (ev) => {
      const idx = hitElement(canvasPos(ev));
      if (idx >= 0) { state.sel = idx; fillZoneInspector(); renderCanvas(); $("z-role").focus(); }
    });

    $("btn-new").addEventListener("click", newZone);
    $("btn-del").addEventListener("click", deleteSelected);
    $("btn-skeleton").addEventListener("click", doSkeleton);
    $("btn-prompt").addEventListener("click", doPrompt);
    $("btn-render").addEventListener("click", doRender);
    $("btn-critic").addEventListener("click", doCritic);
    $("btn-merge").addEventListener("click", doMerge);
    $("btn-cycle").addEventListener("click", runCycle);
    $("btn-validate").addEventListener("click", doValidate);
    $("btn-validate2").addEventListener("click", doValidate);
    $("btn-export").addEventListener("click", exportLayout);
    $("file-import").addEventListener("change", (ev) => {
      const f = ev.target.files && ev.target.files[0];
      if (f) importLayout(f);
      ev.target.value = "";
    });
    $("btn-patterns").addEventListener("click", doPatterns);
    $("btn-apply").addEventListener("click", applyInstruction);
    $("server-status").addEventListener("click", () => {
      toast("Проверяю сервер…", "");
      detectServer();
    });

    // инспектор зоны — запись обратно в state (координаты всегда в пикселях полотна)
    $("z-id").addEventListener("change", () => {
      if (state.sel < 0) return;
      const e = state.layout.elements[state.sel];
      let v = $("z-id").value.trim();
      if (!v) v = autoId();
      const others = state.layout.elements.filter((x, i) => i !== state.sel);
      while (others.some((x) => x.id === v)) v = v + "-2";
      e.id = v;
      $("z-id").value = v;
      renderCanvas();
    });
    $("z-role").addEventListener("input", () => {
      if (state.sel < 0) return;
      state.layout.elements[state.sel].role = $("z-role").value;
      renderCanvas();
    });
    ["z-far", "z-mid", "z-near"].forEach((id) => {
      $(id).addEventListener("click", () => setZ(id.replace("z-", "")));
    });
    $("z-color").addEventListener("input", () => {
      if (state.sel < 0) return;
      state.layout.elements[state.sel].color = $("z-color").value;
      renderCanvas();
    });
    $("z-glow").addEventListener("change", () => {
      if (state.sel < 0) return;
      state.layout.elements[state.sel].glow = $("z-glow").checked;
      renderCanvas();
    });
    ["x0", "y0", "x1", "y1"].forEach((k) => {
      $("z-" + k).addEventListener("input", () => {
        if (state.sel < 0) return;
        const e = state.layout.elements[state.sel];
        const v = Math.round(Number($("z-" + k).value));
        if (!Number.isFinite(v)) return;
        const b = e.bbox.slice();
        b[["x0", "y0", "x1", "y1"].indexOf(k)] = v;
        setBbox(state.sel, b);
        renderCanvas();
        fillZoneInspector();
      });
    });

    // инспектор сцены
    $("s-mood").addEventListener("input", () => { state.layout.style.mood = $("s-mood").value; });
    $("s-light").addEventListener("input", () => { state.layout.style.light = $("s-light").value; });
    ["base", "primary", "accent", "warm"].forEach((k) => {
      $("p-" + k).addEventListener("input", () => {
        state.layout.style.palette[k] = $("p-" + k).value;
        renderCanvas();
      });
    });
    $("s-px").addEventListener("change", () => {
      state.layout.style.perspective[0] = clamp(Number($("s-px").value) || 0, 0, 1);
      renderCanvas();
    });
    $("s-py").addEventListener("change", () => {
      state.layout.style.perspective[1] = clamp(Number($("s-py").value) || 0, 0, 1);
      renderCanvas();
    });

    // полотно
    $("cv-w").addEventListener("change", () => {
      state.layout.canvas.w = clamp(Math.round(Number($("cv-w").value)) || 1280, 64, 4096);
      fillToolbar(); renderCanvas(); fillZoneInspector();
      toast("Полотно " + state.layout.canvas.w + "×" + state.layout.canvas.h + " — проверьте, что зоны внутри", "warn");
    });
    $("cv-h").addEventListener("change", () => {
      state.layout.canvas.h = clamp(Math.round(Number($("cv-h").value)) || 720, 64, 4096);
      fillToolbar(); renderCanvas(); fillZoneInspector();
      toast("Полотно " + state.layout.canvas.w + "×" + state.layout.canvas.h + " — проверьте, что зоны внутри", "warn");
    });

    window.addEventListener("resize", renderCanvas);
  }

  // ================= стартовый лейаут =================
  function starterLayout() {
    const res = normalizeLayout({
      canvas: { w: 1280, h: 720 },
      style: {
        mood: "quiet demo scene, three depth planes",
        palette: { base: "#0b0f1a", primary: "#2f6fed", accent: "#14b8a6", warm: "#f59e0b" },
        light: "soft top light, faint haze",
        perspective: [0.5, 0.47],
      },
      elements: [
        { id: "e1", bbox: [80, 60, 560, 340], z: "far", role: "background wall panel", color: "#2f6fed", glow: false },
        { id: "e2", bbox: [420, 300, 900, 620], z: "mid", role: "main subject block", color: "#14b8a6", glow: true },
        { id: "e3", bbox: [900, 480, 1240, 700], z: "near", role: "foreground table with props", color: "#f59e0b", glow: false },
      ],
    });
    return res.lay;
  }

  // ================= загрузка =================
  function boot() {
    state.layout = starterLayout();
    bindEvents();
    renderHistory();
    refreshAll();
    detectServer();
  }

  // тестовый хук (node: парсер/валидатор проверяются без DOM)
  globalThis.REVO = { normalizeLayout, validateLocal, localPrompt, positionWords, mixHex, hexA, drawOrder };

  if (typeof window !== "undefined" && typeof window.document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }
})();
