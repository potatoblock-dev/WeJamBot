#!/usr/bin/env bash
# Build redistribute-able desktop packages into dist/ (macOS .app and Linux tarball).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
rm -rf "$DIST"
mkdir -p "$DIST/macos" "$DIST/linux"

if [[ "$(uname -s)" == "Darwin" ]]; then
  DESKTOP="$DIST/macos" "$ROOT/scripts/install-desktop-app.sh"
  echo "macOS 包: $DIST/macos/WeJam闲聊控制台.app"
else
  echo "跳过 macOS .app（当前不是 Darwin）。在 Mac 上再跑本脚本可得到 .app。"
fi

STAGE="$DIST/linux/wejam-admin-desktop"
mkdir -p "$STAGE/scripts" "$STAGE/assets"
# Full admin source tree so Linux hosts can venv-install without cloning extra paths.
rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  "$ROOT/bot" "$ROOT/wejam" "$ROOT/examples" "$ROOT/assets" \
  "$ROOT/personas.toml" "$ROOT/reply_policy.toml" "$ROOT/pyproject.toml" "$ROOT/.env.example" \
  "$STAGE/"
cp "$ROOT/scripts/admin-launcher.sh" "$STAGE/scripts/"
cp "$ROOT/scripts/install-desktop-linux.sh" "$STAGE/scripts/"
cp "$ROOT/scripts/start-admin.sh" "$STAGE/scripts/"
cp "$ROOT/scripts/start-bot.sh" "$STAGE/scripts/"
cp "$ROOT/scripts/stop-bot.sh" "$STAGE/scripts/"
chmod +x "$STAGE/scripts/"*.sh
cat >"$STAGE/README.txt" <<'EOF'
WeJam 闲聊控制台 — Linux 桌面包

这不是独立二进制：需要本目录下的 .venv（含 PyQt6）。

  python3 -m venv .venv
  .venv/bin/pip install -e ".[bot]"
  cp .env.example .env   # 填 LLM_API_KEY
  ./scripts/install-desktop-linux.sh

会在 ~/.local/share/applications/ 和桌面（若存在）生成 wejam-admin.desktop。
微信容器仍按仓库 README 用 docker/run.sh 启动。
EOF
tar -C "$DIST/linux" -czf "$DIST/linux/wejam-admin-desktop.tar.gz" wejam-admin-desktop
echo "Linux 包: $DIST/linux/wejam-admin-desktop.tar.gz"
echo "完成。"
