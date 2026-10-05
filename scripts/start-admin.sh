#!/usr/bin/env bash
# Open the PyQt WeJam chat admin window.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "缺少 .venv，先执行: python3 -m venv .venv && .venv/bin/pip install -e '.[bot]'"
  exit 1
fi
cd "$ROOT"
exec "$PY" -m bot.admin.desktop
