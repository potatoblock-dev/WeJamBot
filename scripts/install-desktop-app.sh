#!/usr/bin/env bash
# 在桌面安装「WeJam闲聊控制台.app」（AppleScript 小程序，对齐 0x01 的 QQ机器人管理.app）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="WeJam闲聊控制台"
DESKTOP="${DESKTOP:-$HOME/Desktop}"
APP_DIR="$DESKTOP/${APP_NAME}.app"
CMD_FILE="$DESKTOP/${APP_NAME}.command"
ICON_SRC="$ROOT/assets/app-icon.png"
APPLESCRIPT_SRC="$ROOT/scripts/desktop-app.applescript"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此脚本只在 macOS 上构建 .app。Linux 请用 ./scripts/install-desktop-linux.sh"
  exit 1
fi

if [[ ! -f "$ICON_SRC" ]]; then
  echo "缺少图标: $ICON_SRC"
  exit 1
fi

if [[ ! -f "$APPLESCRIPT_SRC" ]]; then
  echo "缺少 AppleScript: $APPLESCRIPT_SRC"
  exit 1
fi

if ! command -v osacompile >/dev/null 2>&1; then
  echo "需要 osacompile（macOS 自带）"
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "缺少 .venv，请先: python3 -m venv .venv && .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

if ! "$ROOT/.venv/bin/python" -c "import PyQt6" 2>/dev/null; then
  echo "缺少 PyQt6，请执行: .venv/bin/pip install -e '.[bot]'"
  exit 1
fi

build_icns() {
  local src="$1"
  local out="$2"
  local iconset
  iconset="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$iconset"
  local sizes=(16 32 64 128 256 512)
  for size in "${sizes[@]}"; do
    sips -z "$size" "$size" "$src" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$src" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$out"
}

plist_set() {
  local plist="$1"
  local key="$2"
  local value="$3"
  if /usr/libexec/PlistBuddy -c "Print :$key" "$plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$plist"
  else
    /usr/libexec/PlistBuddy -c "Add :$key string $value" "$plist"
  fi
}

sign_app_bundle() {
  local app="$1"
  local exe="$app/Contents/MacOS/applet"
  if ! command -v codesign >/dev/null 2>&1; then
    return 0
  fi
  codesign --force --sign - --timestamp=none "$exe"
  codesign --force --sign - --timestamp=none "$app"
}

register_app() {
  local app="$1"
  local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  if [[ -x "$lsregister" ]]; then
    "$lsregister" -f "$app" >/dev/null 2>&1 || true
  fi
}

mkdir -p "$DESKTOP"
rm -rf "$APP_DIR"
osacompile -o "$APP_DIR" "$APPLESCRIPT_SRC"
printf '%s\n' "$ROOT" >"$APP_DIR/Contents/Resources/project-root.txt"
build_icns "$ICON_SRC" "$APP_DIR/Contents/Resources/AppIcon.icns"

PLIST="$APP_DIR/Contents/Info.plist"
plist_set "$PLIST" CFBundleIconFile AppIcon
plist_set "$PLIST" CFBundleIdentifier com.potatoblock.wejam-admin
plist_set "$PLIST" CFBundleName "$APP_NAME"
plist_set "$PLIST" CFBundleDisplayName "$APP_NAME"
plist_set "$PLIST" CFBundleShortVersionString 0.1.0
plist_set "$PLIST" CFBundleVersion 1
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSHighResolutionCapable true" "$PLIST"

xattr -cr "$APP_DIR" 2>/dev/null || true
sign_app_bundle "$APP_DIR"
register_app "$APP_DIR"
touch "$APP_DIR"

cat >"$CMD_FILE" <<CMD
#!/usr/bin/env bash
cd "$ROOT"
exec "$ROOT/scripts/admin-launcher.sh"
CMD
chmod +x "$CMD_FILE"
xattr -cr "$CMD_FILE" 2>/dev/null || true

echo "已安装:"
echo "  App: $APP_DIR"
echo "  备用: $CMD_FILE"
