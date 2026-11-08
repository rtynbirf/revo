#!/usr/bin/env bash
# =====================================================================
# REVO M2 · tests/smoke_api.sh — смоук HTTP-контракта docs/API.md §2.
#
# Поднимает revo_server.py на СВОБОДНОМ порту (порт ищет python3),
# прогоняет ВСЕ эндпоинты: позитивы (валидный layouts/datacenter.json и
# compose «ночной дата-центр») + негативы (битый layout → 400 с "error"
# в JSON; неизвестный путь → 404; чужой метод → 405).
# Полностью offline: все запросы идут с backend=offline (N-01), zai не зовётся.
# exit 0 — только если всё зелёное. Сервер убивается trap'ом.
# =====================================================================
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0; FAIL=0
TMP="$(mktemp -d /tmp/revo-smoke-XXXXXX)"
LOG="$TMP/server.log"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
BASE="http://127.0.0.1:$PORT"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

ok()  { PASS=$((PASS+1)); echo "PASS  $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL  $1 :: $2"; }

json_get() { # FILE KEY[.KEY] → значение (bool → true/false)
  python3 -c 'import json,sys
try:
    v = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print(""); raise SystemExit
for k in sys.argv[2].split("."):
    v = v.get(k) if isinstance(v, dict) else None
    if v is None: break
if isinstance(v, bool): v = str(v).lower()
elif v is None: v = ""
print(v)' "$1" "$2" 2>/dev/null
}
is_png() { python3 -c 'import sys; sys.exit(0 if open(sys.argv[1],"rb").read(8)==b"\x89PNG\r\n\x1a\n" else 1)' "$1"; }

get() { # PATH OUTFILE → http_code
  curl -s -o "$2" -w '%{http_code}' "$BASE$1"
}
post() { # PATH BODY_JSON OUTFILE → http_code
  curl -s -X POST -H 'Content-Type: application/json' --data-binary @"$2" \
       -D "$TMP/headers.txt" -o "$3" -w '%{http_code}' "$BASE$1"
}

# ---------------------------------------------------------- старт сервера ---
python3 revo_server.py --port "$PORT" >"$LOG" 2>&1 &
SERVER_PID=$!

up=0
for _ in $(seq 1 80); do
  code="$(get /health "$TMP/health.json")"
  [ "$code" = "200" ] && { up=1; break; }
  sleep 0.25
done
if [ "$up" != "1" ]; then
  bad "server-up" "/health не поднялся на порту $PORT"
  echo "---- лог сервера:"; cat "$LOG"
  exit 1
fi
ok "server-up: GET /health → 200 на свободном порту $PORT"

# ------------------------------------------------------------- фикстуры -----
DC="layouts/datacenter.json"
[ -f "$DC" ] || { bad "fixtures" "$DC не найден"; exit 1; }
python3 - "$TMP" <<'PY'
import json, sys
tmp = sys.argv[1]
lay = json.load(open("layouts/datacenter.json", encoding="utf-8"))
json.dump(lay, open(f"{tmp}/lay.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"layout": lay, "backend": "offline"}, open(f"{tmp}/render_req.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"layout": lay, "backend": "offline"}, open(f"{tmp}/pipeline_req.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"task": "ночной дата-центр", "backend": "offline"}, open(f"{tmp}/compose_req.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"task": "караоке клуб, неон", "backend": "offline", "attempts": 3}, open(f"{tmp}/auto_req.json", "w", encoding="utf-8"), ensure_ascii=False)
PY

# ------------------------------------------------------------- позитивы -----
# 1. health
[ "$(json_get "$TMP/health.json" ok)" = "true" ] \
  && ok "GET /health → {ok:true, api, backend, version}" \
  || bad "GET /health" "body=$(cat "$TMP/health.json")"

# 2. validate (валидный layout — всегда 200, valid:true)
code="$(post /api/validate "$TMP/lay.json" "$TMP/validate.json")"
[ "$code" = "200" ] && [ "$(json_get "$TMP/validate.json" valid)" = "true" ] \
  && ok "POST /api/validate (datacenter) → valid:true, errors:[]" \
  || bad "POST /api/validate" "code=$code body=$(cat "$TMP/validate.json" 2>/dev/null)"

# 3. compose («ночной дата-центр», offline-мозг)
code="$(post /api/compose "$TMP/compose_req.json" "$TMP/compose.json")"
[ "$code" = "200" ] && [ -n "$(json_get "$TMP/compose.json" brain)" ] \
  && [ "$(json_get "$TMP/compose.json" errors)" = "[]" ] \
  && ok "POST /api/compose («ночной дата-центр») → brain=$(json_get "$TMP/compose.json" brain)" \
  || bad "POST /api/compose" "code=$code body=$(head -c 300 "$TMP/compose.json" 2>/dev/null)"

# 4. skeleton → PNG-бинарник
code="$(post /api/skeleton "$TMP/lay.json" "$TMP/skeleton.png")"
[ "$code" = "200" ] && is_png "$TMP/skeleton.png" \
  && grep -qi '^content-type: image/png' "$TMP/headers.txt" \
  && ok "POST /api/skeleton → 200 image/png" \
  || bad "POST /api/skeleton" "code=$code png=$(is_png "$TMP/skeleton.png" && echo yes || echo no)"

# 5. prompt
code="$(post /api/prompt "$TMP/lay.json" "$TMP/prompt.json")"
[ "$code" = "200" ] && [ -n "$(json_get "$TMP/prompt.json" prompt)" ] \
  && ok "POST /api/prompt → prompt длиной $(json_get "$TMP/prompt.json" prompt | wc -c) симв." \
  || bad "POST /api/prompt" "code=$code body=$(cat "$TMP/prompt.json" 2>/dev/null)"

# 6. render offline → PNG
code="$(post /api/render "$TMP/render_req.json" "$TMP/render.png")"
[ "$code" = "200" ] && is_png "$TMP/render.png" \
  && ok "POST /api/render (backend=offline) → 200 image/png" \
  || bad "POST /api/render" "code=$code png=$(is_png "$TMP/render.png" 2>/dev/null && echo yes || echo no)"

# 7. pipeline offline → отчёт §3.2 + файлы в tmp
code="$(post /api/pipeline "$TMP/pipeline_req.json" "$TMP/pipeline.json")"
pl_render="$(json_get "$TMP/pipeline.json" render)"
[ "$code" = "200" ] && [ "$(json_get "$TMP/pipeline.json" backend)" = "offline" ] \
  && [ -f "$pl_render" ] \
  && ok "POST /api/pipeline (offline) → отчёт §3.2, render=$pl_render" \
  || bad "POST /api/pipeline" "code=$code render=$pl_render body=$(head -c 300 "$TMP/pipeline.json" 2>/dev/null)"

# 8. critic (layout + base64 рендера из шага 6)
python3 - "$TMP/critic_req.json" "$TMP/render.png" <<'PY'
import json, sys, base64
lay = json.load(open("layouts/datacenter.json", encoding="utf-8"))
b64 = base64.b64encode(open(sys.argv[2], "rb").read()).decode("ascii")
json.dump({"layout": lay, "render_png_base64": b64}, open(sys.argv[1], "w", encoding="utf-8"))
PY
code="$(post /api/critic "$TMP/critic_req.json" "$TMP/critic.json")"
[ "$code" = "200" ] && [ -n "$(json_get "$TMP/critic.json" overall)" ] \
  && ok "POST /api/critic → overall=$(json_get "$TMP/critic.json" overall), pass=$(json_get "$TMP/critic.json" pass)" \
  || bad "POST /api/critic" "code=$code body=$(head -c 300 "$TMP/critic.json" 2>/dev/null)"

# 9. auto (полный цикл offline: мозг → рендер → критик → рефайн)
code="$(post /api/auto "$TMP/auto_req.json" "$TMP/auto.json")"
au_pipe="$(json_get "$TMP/auto.json" pipeline)"
[ "$code" = "200" ] && [ -n "$(json_get "$TMP/auto.json" score)" ] \
  && [ -f "$au_pipe" ] \
  && ok "POST /api/auto (offline) → score=$(json_get "$TMP/auto.json" score), pass=$(json_get "$TMP/auto.json" pass), best=$(json_get "$TMP/auto.json" best_attempt)" \
  || bad "POST /api/auto" "code=$code pipeline=$au_pipe body=$(head -c 300 "$TMP/auto.json" 2>/dev/null)"

# 10. patterns
code="$(get /api/patterns "$TMP/patterns.json")"
pats_ok="$(python3 -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
ps = d.get("patterns")
print("yes" if isinstance(ps, list) and len(ps) == 8 and all({"name","zones","kw"} <= set(p) and isinstance(p["kw"], list) for p in ps) else "no")' "$TMP/patterns.json" 2>/dev/null)"
[ "$code" = "200" ] && [ "$pats_ok" = "yes" ] \
  && ok "GET /api/patterns → 8 архетипов {name,zones,kw}" \
  || bad "GET /api/patterns" "code=$code ok=$pats_ok"

# 11. статика / (app/ может ещё не существовать — параллельный агент UI)
if [ -f app/index.html ]; then
  code="$(get / "$TMP/index.html")"
  [ "$code" = "200" ] && ok "GET / → 200 (app/index.html)" || bad "GET /" "code=$code"
else
  code="$(get / "$TMP/noroot.json")"
  [ "$code" = "404" ] && [ -n "$(json_get "$TMP/noroot.json" error)" ] \
    && ok "GET / без app/ → 404 JSON с error (graceful, API жив)" \
    || bad "GET / без app/" "code=$code"
fi

# ------------------------------------------------------------- негативы -----
# N1: битый JSON → 400 с машиночитаемым error (F-17)
printf '{oops, это не json' >"$TMP/bad_json.json"
code="$(post /api/validate "$TMP/bad_json.json" "$TMP/n1.json")"
[ "$code" = "400" ] && [ -n "$(json_get "$TMP/n1.json" error)" ] \
  && ok "негатив: битый JSON → 400 error=$(json_get "$TMP/n1.json" error)" \
  || bad "негатив: битый JSON" "code=$code body=$(cat "$TMP/n1.json" 2>/dev/null)"

# N2: битый layout → 400 с "error" в JSON
printf '{"canvas": {"w": 10, "h": 10}, "elements": []}' >"$TMP/bad_layout.json"
code="$(post /api/skeleton "$TMP/bad_layout.json" "$TMP/n2.json")"
[ "$code" = "400" ] && [ -n "$(json_get "$TMP/n2.json" error)" ] \
  && ok "негатив: битый layout → 400 error=$(json_get "$TMP/n2.json" error)" \
  || bad "негатив: битый layout" "code=$code body=$(cat "$TMP/n2.json" 2>/dev/null)"

# N3: неизвестный путь → 404
code="$(get /no-such-path "$TMP/n3.json")"
[ "$code" = "404" ] && [ -n "$(json_get "$TMP/n3.json" error)" ] \
  && ok "негатив: GET /no-such-path → 404 error=$(json_get "$TMP/n3.json" error)" \
  || bad "негатив: неизвестный путь" "code=$code"

# N4: чужой метод → 405
code="$(get /api/render "$TMP/n4.json")"
[ "$code" = "405" ] && [ -n "$(json_get "$TMP/n4.json" error)" ] \
  && ok "негатив: GET /api/render → 405 error=$(json_get "$TMP/n4.json" error)" \
  || bad "негатив: чужой метод" "code=$code"

# N5: неизвестный backend → 400
printf '{"layout": {"canvas": {"w": 640, "h": 360}, "elements": [{"id": "a", "bbox": [10, 10, 300, 200], "z": "far"}]}, "backend": "warp"}' >"$TMP/n5.json"
code="$(post /api/render "$TMP/n5.json" "$TMP/n5r.json")"
[ "$code" = "400" ] && [ "$(json_get "$TMP/n5r.json" error)" = "bad_backend" ] \
  && ok "негатив: backend=warp → 400 bad_backend" \
  || bad "негатив: плохой backend" "code=$code"

# N6: кривой base64 в критике → 400
printf '{"layout": {"canvas": {"w": 640, "h": 360}, "elements": [{"id": "a", "bbox": [10, 10, 300, 200], "z": "far"}]}, "render_png_base64": "@@@(не base64"}' >"$TMP/n6.json"
code="$(post /api/critic "$TMP/n6.json" "$TMP/n6r.json")"
[ "$code" = "400" ] && [ -n "$(json_get "$TMP/n6r.json" error)" ] \
  && ok "негатив: битый base64 в /api/critic → 400 error=$(json_get "$TMP/n6r.json" error)" \
  || bad "негатив: битый base64" "code=$code"

# ------------------------------------------------------------------ итог ----
echo "---- smoke: PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" != "0" ]; then
  echo "---- хвост лога сервера:"
  tail -n 30 "$LOG"
  exit 1
fi
exit 0
