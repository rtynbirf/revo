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

get() { # PATH OUTFILE → http_code (заодно пишет $TMP/headers.txt)
  curl -s -o "$2" -D "$TMP/headers.txt" -w '%{http_code}' "$BASE$1"
}
post() { # PATH BODY_JSON OUTFILE → http_code
  curl -s -X POST -H 'Content-Type: application/json' --data-binary @"$2" \
       -D "$TMP/headers.txt" -o "$3" -w '%{http_code}' "$BASE$1"
}

# ---------------------------------------------------------- старт сервера ---
python3 revo_server.py --port "$PORT" --projects-dir "$TMP/projects" >"$LOG" 2>&1 &
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
# M4-фикстуры: создание проекта (datacenter) + версия 2 (добавлена зона smoke-extra)
json.dump({"name": "smoke-m4", "layout": lay, "source": "LLM", "note": "фикстура смоука"},
          open(f"{tmp}/prj_create_req.json", "w", encoding="utf-8"), ensure_ascii=False)
import copy
lay2 = copy.deepcopy(lay)
lay2["elements"].append({"id": "smoke-extra", "bbox": [10, 10, 120, 90], "z": "near", "color": "#f59e0b"})
json.dump({"layout": lay2, "source": "рука", "note": "добавлена smoke-extra"},
          open(f"{tmp}/prj_v2_req.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"layout": {"canvas": {"w": 640, "h": 360}, "elements": []}},
          open(f"{tmp}/prj_bad_req.json", "w", encoding="utf-8"), ensure_ascii=False)
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

# 6b. F-25 (M5): render плагином реестра (poster) → 200 PNG без правки ядра
printf '{"layout": ' >"$TMP/render_poster_req.json"
cat "$DC" >>"$TMP/render_poster_req.json"
printf ', "backend": "poster"}' >>"$TMP/render_poster_req.json"
code="$(post /api/render "$TMP/render_poster_req.json" "$TMP/render_poster.png")"
[ "$code" = "200" ] && is_png "$TMP/render_poster.png" \
  && ok "F-25: POST /api/render (backend=poster, плагин реестра) → 200 image/png" \
  || bad "F-25: render плагином" "code=$code"

# 6c. F-25 (M5): чужое имя бэкенда → 400 bad_backend (реестр честный)
printf '{"layout": ' >"$TMP/render_bad_req.json"
cat "$DC" >>"$TMP/render_bad_req.json"
printf ', "backend": "net-takogo-bekenda"}' >>"$TMP/render_bad_req.json"
code="$(post /api/render "$TMP/render_bad_req.json" "$TMP/render_bad.json")"
[ "$code" = "400" ] && [ "$(json_get "$TMP/render_bad.json" error)" = "bad_backend" ] \
  && ok "F-25: POST /api/render чужим бэкендом → 400 bad_backend (список реестра в detail)" \
  || bad "F-25: чужой бэкенд" "code=$code body=$(cat "$TMP/render_bad.json" 2>/dev/null)"

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

# ------------------------------------------------------------- M4: проекты ---
# M4-1: пустой список проектов
code="$(get /api/projects "$TMP/prj_list0.json")"
[ "$code" = "200" ] && [ "$(json_get "$TMP/prj_list0.json" projects)" = "[]" ] \
  && ok "M4: GET /api/projects (пусто) → {projects:[]}" \
  || bad "M4: список пуст" "code=$code body=$(cat "$TMP/prj_list0.json" 2>/dev/null)"

# M4-2: создание проекта (F-22: v1 + диф от пустоты)
code="$(post /api/projects "$TMP/prj_create_req.json" "$TMP/prj_create.json")"
PRJ_ID="$(json_get "$TMP/prj_create.json" id)"
[ "$code" = "200" ] && [ -n "$PRJ_ID" ] && [ "$(json_get "$TMP/prj_create.json" n)" = "1" ] \
  && ok "M4: POST /api/projects → id=$PRJ_ID, v1" \
  || bad "M4: создание" "code=$code body=$(head -c 300 "$TMP/prj_create.json" 2>/dev/null)"

# M4-3: мета проекта
[ -n "$PRJ_ID" ] && code="$(get "/api/projects/$PRJ_ID" "$TMP/prj_meta.json")" || code=000
vcount="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1],encoding="utf-8"))["versions"]))' "$TMP/prj_meta.json" 2>/dev/null)"
[ "$code" = "200" ] && [ "$(json_get "$TMP/prj_meta.json" name)" = "smoke-m4" ] && [ "$vcount" = "1" ] \
  && ok "M4: GET /api/projects/<id> → name=smoke-m4, versions=1" \
  || bad "M4: мета" "code=$code versions=$vcount body=$(head -c 200 "$TMP/prj_meta.json" 2>/dev/null)"

# M4-4: версия 1 несёт layout (datacenter, 7 зон)
code="$(get "/api/projects/$PRJ_ID/versions/1" "$TMP/prj_v1.json")"
v1_zones="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1],encoding="utf-8"))["layout"]["elements"]))' "$TMP/prj_v1.json" 2>/dev/null)"
[ "$code" = "200" ] && [ "$v1_zones" = "7" ] \
  && ok "M4: GET versions/1 → layout с 7 зонами datacenter" \
  || bad "M4: версия 1" "code=$code zones=$v1_zones"

# M4-5: версия 2 + серверный диф (added=[smoke-extra]) — числа только от diff_zones
code="$(post "/api/projects/$PRJ_ID/versions" "$TMP/prj_v2_req.json" "$TMP/prj_v2.json")"
[ "$code" = "200" ] && [ "$(json_get "$TMP/prj_v2.json" n)" = "2" ] \
  && [ "$(json_get "$TMP/prj_v2.json" diff.added)" = "['smoke-extra']" ] \
  && ok "M4: POST versions → n=2, diff.added=[smoke-extra] (revo.diff_zones)" \
  || bad "M4: версия 2 + диф" "code=$code body=$(head -c 300 "$TMP/prj_v2.json" 2>/dev/null)"

# M4-6: чтение версии 2
code="$(get "/api/projects/$PRJ_ID/versions/2" "$TMP/prj_v2full.json")"
v2_extra="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print(any(e["id"]=="smoke-extra" for e in d["layout"]["elements"]))' "$TMP/prj_v2full.json" 2>/dev/null)"
[ "$code" = "200" ] && [ "$v2_extra" = "True" ] && [ "$(json_get "$TMP/prj_v2full.json" source)" = "рука" ] \
  && ok "M4: GET versions/2 → layout со smoke-extra, source=рука" \
  || bad "M4: чтение версии 2" "code=$code extra=$v2_extra"

# M4-7: экспорт ZIP (F-23) — магия PK + application/zip
code="$(get "/api/projects/$PRJ_ID/export" "$TMP/prj_export.zip")"
zip_magic="$(head -c 4 "$TMP/prj_export.zip" 2>/dev/null)"
[ "$code" = "200" ] && [ "$zip_magic" = $'PK\x03\x04' ] \
  && grep -qi '^content-type: application/zip' "$TMP/headers.txt" \
  && ok "M4: GET export → ZIP (revo-project/v1), PK-магия" \
  || bad "M4: экспорт" "code=$code magic=$zip_magic"

# M4-8: импорт ZIP — новый id (renamed: true, никогда не перезаписываем)
python3 - "$TMP/prj_import_req.json" "$TMP/prj_export.zip" <<'PY'
import json, sys, base64
b64 = base64.b64encode(open(sys.argv[2], "rb").read()).decode("ascii")
json.dump({"zip_base64": b64}, open(sys.argv[1], "w", encoding="utf-8"))
PY
code="$(post /api/projects/import "$TMP/prj_import_req.json" "$TMP/prj_import.json")"
PRJ_ID2="$(json_get "$TMP/prj_import.json" id)"
[ "$code" = "200" ] && [ -n "$PRJ_ID2" ] && [ "$PRJ_ID2" != "$PRJ_ID" ] \
  && [ "$(json_get "$TMP/prj_import.json" renamed)" = "true" ] \
  && [ "$(json_get "$TMP/prj_import.json" versions)" = "2" ] \
  && ok "M4: POST import → id=$PRJ_ID2 (renamed=true), 2 версии" \
  || bad "M4: импорт" "code=$code body=$(head -c 300 "$TMP/prj_import.json" 2>/dev/null)"

# M4-9: приёмка 1:1 после экспорт→импорт (SHA256 layout v2 + diff + source)
code="$(get "/api/projects/$PRJ_ID2/versions/2" "$TMP/prj_v2imp.json")"
one2one="$(python3 - "$TMP/prj_v2full.json" "$TMP/prj_v2imp.json" <<'PY'
import json, sys, hashlib
sha = lambda v: hashlib.sha256(json.dumps(v["layout"], sort_keys=True).encode()).hexdigest()
a, b = json.load(open(sys.argv[1], encoding="utf-8")), json.load(open(sys.argv[2], encoding="utf-8"))
print("OK" if sha(a) == sha(b) and a.get("diff") == b.get("diff") and a.get("source") == b.get("source") else "MISMATCH")
PY
)"
[ "$code" = "200" ] && [ "$one2one" = "OK" ] \
  && ok "M4: экспорт→импорт воспроизводит 1:1 (SHA256 layout v2 + diff + source)" \
  || bad "M4: приёмка 1:1" "code=$code verdict=$one2one"

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

# N7: несуществующий проект → 404 project_not_found
code="$(get /api/projects/no-such-pid "$TMP/n7.json")"
[ "$code" = "404" ] && [ "$(json_get "$TMP/n7.json" error)" = "project_not_found" ] \
  && ok "негатив: GET /api/projects/no-such-pid → 404 project_not_found" \
  || bad "негатив: чужой проект" "code=$code body=$(cat "$TMP/n7.json" 2>/dev/null)"

# N8: несуществующая версия → 404 version_not_found
[ -n "$PRJ_ID" ] && code="$(get "/api/projects/$PRJ_ID/versions/99" "$TMP/n8.json")" || code=000
[ "$code" = "404" ] && [ "$(json_get "$TMP/n8.json" error)" = "version_not_found" ] \
  && ok "негатив: GET versions/99 → 404 version_not_found" \
  || bad "негатив: чужая версия" "code=$code"

# N9: битый layout при создании проекта → 400 invalid_layout
code="$(post /api/projects "$TMP/prj_bad_req.json" "$TMP/n9.json")"
[ "$code" = "400" ] && [ "$(json_get "$TMP/n9.json" error)" = "invalid_layout" ] \
  && ok "негатив: POST /api/projects с пустым elements → 400 invalid_layout" \
  || bad "негатив: битый проект" "code=$code body=$(cat "$TMP/n9.json" 2>/dev/null)"

# N10: битый layout при добавлении версии → 400 (проект не затронут)
[ -n "$PRJ_ID" ] && code="$(post "/api/projects/$PRJ_ID/versions" "$TMP/prj_bad_req.json" "$TMP/n10.json")" || code=000
[ "$code" = "400" ] && [ "$(json_get "$TMP/n10.json" error)" = "invalid_layout" ] \
  && ok "негатив: POST versions с битым layout → 400 invalid_layout" \
  || bad "негатив: битая версия" "code=$code"

# N11: traversal в id → отказ (404/400), ноль записей вне projects/
code="$(curl -s --path-as-is -o "$TMP/n11.json" -w '%{http_code}' "$BASE/api/projects/..%2F..%2Fetc")"
[ "$code" = "400" ] || [ "$code" = "404" ] \
  && ok "негатив: traversal ..%2F.. в id → $code (запись за пределы projects/ исключена)" \
  || bad "негатив: traversal" "code=$code"

# N12: битый base64 в импорте → 400 bad_base64
printf '{"zip_base64": "@@@не-base64@@@"}' >"$TMP/n12.json"
code="$(post /api/projects/import "$TMP/n12.json" "$TMP/n12r.json")"
[ "$code" = "400" ] && [ "$(json_get "$TMP/n12r.json" error)" = "bad_base64" ] \
  && ok "негатив: битый base64 в import → 400 bad_base64" \
  || bad "негатив: битый base64 импорта" "code=$code"

# N13: не-ZIP в импорте → 400 bad_zip
python3 - "$TMP/n13.json" <<'PY'
import json, base64, sys
json.dump({"zip_base64": base64.b64encode(b'this is not a zip file at all').decode()}, open(sys.argv[1], "w"))
PY
code="$(post /api/projects/import "$TMP/n13.json" "$TMP/n13r.json")"
[ "$code" = "400" ] && [ "$(json_get "$TMP/n13r.json" error)" = "bad_zip" ] \
  && ok "негатив: не-ZIP в import → 400 bad_zip" \
  || bad "негатив: не-ZIP" "code=$code body=$(cat "$TMP/n13r.json" 2>/dev/null)"

# ------------------------------------------------------------------ итог ----
echo "---- smoke: PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" != "0" ]; then
  echo "---- хвост лога сервера:"
  tail -n 30 "$LOG"
  exit 1
fi
exit 0
