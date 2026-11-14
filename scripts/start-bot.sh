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
# 清掉父进程（如闲聊控制台）里可能残留的旧 LLM_*，避免盖住 .env
unset LLM_API_KEY LLM_BASE_URL LLM_MODEL LLM_TIMEOUT_SECONDS || true
: >"$LOG_FILE"

# Detach into a new session so Cursor/agent shell teardown cannot kill the runner.
WEJAM_BOT_ROOT="$ROOT" "$PY" - <<'PY'
import os
import subprocess
from pathlib import Path

root = Path(os.environ["WEJAM_BOT_ROOT"])
pid_file = root / "data" / "bot.pid"
log_file = root / "data" / "bot.log"
py = root / ".venv" / "bin" / "python"
env = os.environ.copy()
env["PYTHONUNBUFFERED"] = "1"
with log_file.open("a", encoding="utf-8") as log:
    proc = subprocess.Popen(
        [str(py), "-u", "-m", "bot"],
        cwd=str(root),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
print(f"bot 已启动 pid={proc.pid} 日志 {log_file}")
PY
