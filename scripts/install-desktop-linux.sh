#!/usr/bin/env bash
# Install a Linux .desktop launcher for the WeJam admin console (venv-based, like the macOS .app).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="WeJam 闲聊控制台"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_SRC="$ROOT/assets/app-icon.png"
LAUNCHER="$ROOT/scripts/admin-launcher.sh"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "缺少 .venv，请先: python3 -m venv .venv && .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

if ! "$ROOT/.venv/bin/python" -c "import PyQt6" 2>/dev/null; then
  echo "缺少 PyQt6，请执行: .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

mkdir -p "$APPS_DIR"
DESKTOP_FILE="$APPS_DIR/wejam-admin.desktop"
cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
Comment=WeJamBot 宿主闲聊控制台
Exec=/bin/bash $LAUNCHER
Icon=$ICON_SRC
Terminal=false
Categories=Utility;Network;
StartupNotify=true
EOF
chmod +x "$DESKTOP_FILE"

if [[ -d "$DESKTOP_DIR" ]]; then
  cp "$DESKTOP_FILE" "$DESKTOP_DIR/wejam-admin.desktop"
  chmod +x "$DESKTOP_DIR/wejam-admin.desktop"
  echo "已安装到桌面: $DESKTOP_DIR/wejam-admin.desktop"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "已安装到应用菜单: $DESKTOP_FILE"
echo "也可直接: $LAUNCHER"
