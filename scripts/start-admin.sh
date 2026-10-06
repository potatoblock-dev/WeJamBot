#!/usr/bin/env bash
# 打开原生桌面管理窗口（PyQt6）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/admin-launcher.sh"
