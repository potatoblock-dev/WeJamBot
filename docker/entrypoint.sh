#!/bin/bash
# WeJamBot 会话容器入口。
#
# 启动顺序：
#   1. root 初始化（仅首次/每次启动都要做）
#      - machine-id 必须持久化到卷：容器可写层每次重建都会重新生成，
#        设备指纹一变微信就要求重新扫码
#      - 建好 /tmp/.X11-unix 并放开权限
#   2. 降权到 wx（微信不应以 root 运行）
#   3. Xvfb → 会话总线 → 无障碍总线 → 微信 → agent
#
# 本脚本不依赖宿主提供任何东西：X 在容器内，端口经 publish 暴露。
set -u

WEJAM_DATA="${WEJAM_DATA:-/data}"
WX_UID=1000
WX_GID=1000

# ---------------------------------------------------------------------------
# 1. root 初始化
# ---------------------------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    echo "[init] 初始化状态目录 $WEJAM_DATA"
    mkdir -p "$WEJAM_DATA/home" "$WEJAM_DATA/run"

    if [ ! -s "$WEJAM_DATA/machine-id" ]; then
        head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$WEJAM_DATA/machine-id"
        printf '\n' >> "$WEJAM_DATA/machine-id"
        echo "[init] 生成新的 machine-id"
    fi
    cat "$WEJAM_DATA/machine-id" > /etc/machine-id
    cp -f /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null || true
    echo "[init] machine-id = $(cat /etc/machine-id)"

    mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
    chown -R "$WX_UID:$WX_GID" "$WEJAM_DATA"
    chmod 700 "$WEJAM_DATA/run"

    # 降权后重跑自己
    exec setpriv --reuid="$WX_UID" --regid="$WX_GID" --clear-groups "$0" "$@"
fi

# ---------------------------------------------------------------------------
# 2. 以下均以 wx 身份运行
# ---------------------------------------------------------------------------
export HOME="${HOME:-$WEJAM_DATA/home}"
export DISPLAY="${DISPLAY:-:77}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$WEJAM_DATA/run}"

echo "[entrypoint] HOME=$HOME DISPLAY=$DISPLAY uid=$(id -u)"

# ---------- Xvfb ----------
echo "[entrypoint] 启动 Xvfb 1280x900x24 ..."
Xvfb "$DISPLAY" -screen 0 1280x900x24 -ac -noreset -nolisten tcp &
XVFB_PID=$!

for _ in $(seq 1 60); do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    sleep 0.2
done
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "[entrypoint] 致命: Xvfb 启动失败" >&2
    exit 1
fi
echo "[entrypoint] Xvfb 就绪 (pid $XVFB_PID)"

# ---------- 会话总线（AT-SPI 前提） ----------
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS
echo "$DBUS_SESSION_BUS_ADDRESS" > "$HOME/.session_bus_addr"
echo "[entrypoint] 会话总线: $DBUS_SESSION_BUS_ADDRESS"

# ---------- 无障碍总线 ----------
export ACCESSIBILITY_ENABLED=1
export GTK_MODULES=atk-bridge
ATSPI_LAUNCHER=""
for cand in /usr/libexec/at-spi-bus-launcher /usr/lib/at-spi2-core/at-spi-bus-launcher; do
    [ -x "$cand" ] && ATSPI_LAUNCHER="$cand" && break
done
if [ -n "$ATSPI_LAUNCHER" ]; then
    "$ATSPI_LAUNCHER" --launch-immediately >/dev/null 2>&1 &
    echo "[entrypoint] 无障碍总线已拉起"
else
    echo "[entrypoint] 警告: 找不到 at-spi-bus-launcher"
fi
sleep 1

# 输入法相关变量彻底清掉：容器内没有 IME，保证注入的按键原样进到微信
unset QT_IM_MODULE GTK_IM_MODULE QT_IM_MODULES
export XMODIFIERS=@im=none

# ---------- 微信 ----------
WECHAT_BIN="${WEJAM_WECHAT_BIN:-/opt/wechat/wechat}"
echo "[entrypoint] 启动微信 $WECHAT_BIN ..."
"$WECHAT_BIN" > "$HOME/wechat.log" 2>&1 &
WECHAT_PID=$!
echo "[entrypoint] 微信 pid=$WECHAT_PID"

# ---------- Agent（gRPC 服务，持有登录状态机） ----------
echo "[entrypoint] 启动 agent ..."
PYTHONPATH=/opt/wejam /opt/wejam/venv/bin/python -m agent.main \
    > "$HOME/agent.log" 2>&1 &
AGENT_PID=$!
echo "[entrypoint] agent pid=$AGENT_PID"

# ---------- 保活 ----------
while true; do
    if ! kill -0 "$WECHAT_PID" 2>/dev/null; then
        echo "[entrypoint] 微信进程已退出，2 秒后重启"
        sleep 2
        "$WECHAT_BIN" >> "$HOME/wechat.log" 2>&1 &
        WECHAT_PID=$!
        echo "[entrypoint] 微信重启 pid=$WECHAT_PID"
    fi
    if ! kill -0 "$AGENT_PID" 2>/dev/null; then
        echo "[entrypoint] agent 已退出，2 秒后重启"
        sleep 2
        PYTHONPATH=/opt/wejam /opt/wejam/venv/bin/python -m agent.main \
            >> "$HOME/agent.log" 2>&1 &
        AGENT_PID=$!
        echo "[entrypoint] agent 重启 pid=$AGENT_PID"
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "[entrypoint] 致命: Xvfb 死了，退出容器" >&2
        exit 1
    fi
    sleep 3
done
