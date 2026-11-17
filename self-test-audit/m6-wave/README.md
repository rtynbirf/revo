# self-test-audit · волна M6 (2026-11-18)

Claims-пакет волны M6 по канону проверки claims (AGENTS.md, мандат хозяина 2026-11-16).

- база волны: `3caf60b`
- `audit_claims.json` — 7 claims (F-27 зоны · F-27 валидатор · F-27 предупреждение · F-28 кадры · F-28 детерминизм · selftest 24/24 · smoke 36/0)
- `evidence/m6_results.csv` — числа пишутся машиной, не руками (raw_data для measurement)
- `evidence/m6_selftest.log` — дословный лог selftest · `evidence/m6_live.log` — дословный лог живого прогона · `evidence/m6_sha_check.log` — оба SHA256 GIF
- `BUILD_GRAPH.json` — структурная сторона (HUMAN-LFS-стиль: source/status/falsification/depends_on)
- `run_check.sh` — прогон аудита: `LEX_NATURAE=/path/to/lex-naturae ./run_check.sh`

Честно: STRUCTURE-сторона оформлена в духе HUMAN-LFS v0.4; полный прогон validate_graph.py
из singular-a0-audit в этой сессии не выполнялся — обязательная содержательная проверка
(lex-naturae valid=true) выполнена и её вывод приложен в evidence/lex_naturae_output.txt.
