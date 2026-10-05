#!/usr/bin/env bash
# Stop the host-side WeChat chat runner.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/data/bot.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "没有 pid 文件，runner 未在跑"
  exit 0
fi
pid="$(cat "$PID_FILE")"
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid" 2>/dev/null || true
  sleep 0.3
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  echo "已停止 pid=$pid"
else
  echo "pid $pid 已不存在"
fi
rm -f "$PID_FILE"
