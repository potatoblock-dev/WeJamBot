#!/bin/bash
# 启动 WeJamBot 会话容器。
#
# 可移植性设计（这是本脚本的全部意义）：
#   * 不挂载宿主的任何路径 —— 微信、agent、stub 全在镜像里
#   * 不使用 --network host —— 只 publish 一个本地端口，Docker Desktop 同样可用
#   * 状态放命名卷 —— 与代码目录解耦，容器重建不丢登录态
#   * machine-id 由 entrypoint 在卷里持久化 —— 设备指纹稳定，不会反复要求扫码
#
# 用法:
#   ./run.sh                  # 默认 127.0.0.1:7700
#   WEJAM_PORT=8123 ./run.sh  # 换端口
set -eu

NAME="${WEJAM_NAME:-wejam-session}"
IMAGE="${WEJAM_IMAGE:-wejam/wechat:latest}"
VOLUME="${WEJAM_VOLUME:-wejam-data}"
PORT="${WEJAM_PORT:-7700}"

# GUI / 非登录 shell 常没有 ~/.local/bin；允许 DOCKER_BIN 或本机常见路径。
if [[ -z "${DOCKER_BIN:-}" ]]; then
    if command -v docker >/dev/null 2>&1; then
        DOCKER_BIN="$(command -v docker)"
    elif [[ -x "${HOME}/.local/bin/docker" ]]; then
        DOCKER_BIN="${HOME}/.local/bin/docker"
    elif [[ -x /usr/local/bin/docker ]]; then
        DOCKER_BIN=/usr/local/bin/docker
    elif [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
        DOCKER_BIN=/Applications/Docker.app/Contents/Resources/bin/docker
    else
        echo "docker: command not found（请安装 Docker Desktop 或把 docker 放进 PATH）" >&2
        exit 127
    fi
fi

"$DOCKER_BIN" rm -f "$NAME" >/dev/null 2>&1 || true
"$DOCKER_BIN" volume create "$VOLUME" >/dev/null

"$DOCKER_BIN" run -d --name "$NAME" \
    -p "127.0.0.1:${PORT}:7700" \
    -v "${VOLUME}:/data" \
    --restart unless-stopped \
    "$IMAGE"

echo "容器 $NAME 已启动"
echo "  gRPC   : 127.0.0.1:${PORT}"
echo "  状态卷 : ${VOLUME}"
echo
echo "客户端示例:"
echo "  WEJAM_TARGET=127.0.0.1:${PORT} python -m wejam.cli status"
