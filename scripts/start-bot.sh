#!/usr/bin/env bash
# Start the host-side WeChat chat runner (python -m bot).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/data"
PID_FILE="$PID_DIR/bot.pid"
LOG_FILE="$PID_DIR/bot.log"
PY="$ROOT/.venv/bin/python"
mkdir -p "$PID_DIR"

if [[ -f "$ROOT/.env" ]]; then
  chmod 600 "$ROOT/.env"
fi

if [[ ! -x "$PY" ]]; then
  echo "缺少 .venv，先执行: python3 -m venv .venv && .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "bot 已在运行 pid=$(cat "$PID_FILE")"
  exit 0
fi

cd "$ROOT"
: >"$LOG_FILE"
nohup "$PY" -m bot >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
echo "bot 已启动 pid=$(cat "$PID_FILE") 日志 $LOG_FILE"
