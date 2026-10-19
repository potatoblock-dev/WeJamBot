#!/usr/bin/env bash
# Launch the native admin window; log failures for double-click debugging.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/admin-desktop.log"
PY="$ROOT/.venv/bin/python"
mkdir -p "$ROOT/data"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"
}

alert() {
  if [[ "$(uname -s)" == "Darwin" ]] && command -v osascript >/dev/null 2>&1; then
    /usr/bin/osascript -e "display alert \"WeJam 闲聊控制台\" message \"$1\"" >/dev/null 2>&1 || true
  else
    printf '%s\n' "$1" >&2
  fi
}

if [[ ! -x "$PY" ]]; then
  log "missing venv python at $PY"
  alert "找不到 .venv。请在项目目录执行: python3 -m venv .venv && .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

if ! "$PY" -c "import PyQt6" 2>>"$LOG"; then
  log "PyQt6 import failed"
  alert "缺少 PyQt6。请执行: .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

cd "$ROOT"
export PYTHONPATH="$ROOT"
# .app 启动时 PATH 很短；补上本地 docker CLI，供控制台一键启停容器。
export PATH="${HOME}/.local/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:${PATH}"
log "starting admin desktop"

if [[ "$(uname -s)" == "Darwin" && "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then
  arch -arm64 "$PY" -m bot.admin.desktop >>"$LOG" 2>&1
else
  "$PY" -m bot.admin.desktop >>"$LOG" 2>&1
fi
code=$?
log "admin desktop exited code=$code"
exit "$code"
