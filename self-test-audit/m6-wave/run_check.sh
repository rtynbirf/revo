#!/usr/bin/env bash
# Канон проверки claims (AGENTS.md, мандат 2026-11-16): содержание — lex-naturae.
# Использование: LEX_NATURAE=/path/to/lex-naturae ./run_check.sh
set -e
cd "$(dirname "$0")"
: "${LEX_NATURAE:?укажи LEX_NATURAE=/path/to/lex-naturae}"
python3 "$LEX_NATURAE/lex_naturae.py" audit_claims.json
