#!/bin/bash
# macOS .app entry: read project path from the bundle and start PyQt admin.
set -euo pipefail

MACOS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_FILE="$MACOS_DIR/../Resources/project-root.txt"

alert() {
  /usr/bin/osascript -e "display alert \"WeJam 闲聊控制台\" message \"$1\"" >/dev/null 2>&1 || true
}

if [[ ! -f "$ROOT_FILE" ]]; then
  alert "App 配置缺失，请在项目目录重新运行 ./scripts/install-desktop-app.sh"
  exit 1
fi

ROOT="$(tr -d '\r\n' <"$ROOT_FILE")"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  alert "找不到项目 .venv。请在项目目录安装依赖后重装 App。"
  exit 1
fi

exec "$ROOT/scripts/admin-launcher.sh"
