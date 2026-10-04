FROM wejam/ubuntu-base:24.04

# 微信来源：官方 CDN。三种架构都支持，构建时用 --build-arg 切换。
#   https://linux.weixin.qq.com/  （官方下载页）
ARG WECHAT_ARCH=x86_64
ARG WECHAT_DEB_URL=https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_${WECHAT_ARCH}.deb

# 国内源
RUN sed -i 's@//.*archive.ubuntu.com@//mirrors.tuna.tsinghua.edu.cn@g; s@//security.ubuntu.com@//mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true

ENV DEBIAN_FRONTEND=noninteractive

# Xvfb（虚拟屏）+ 微信运行所需的 X/Chromium 客户端库 + 中文字体
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        libx11-6 libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 \
        libxcb-xinerama0 libxcb-xkb1 libxkbcommon0 libxkbcommon-x11-0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxtst6 libxi6 \
        libxcursor1 libxext6 libxrender1 libxshmfence1 libxss1 \
        libgl1 libglx-mesa0 libegl1 libgbm1 libdrm2 \
        libglib2.0-0t64 libdbus-1-3 libnss3 libnspr4 libexpat1 \
        libfontconfig1 libfreetype6 libpng16-16t64 libbz2-1.0 libbrotli1 \
        libpcre2-8-0 libffi8 libudev1 libsystemd0 libselinux1 \
        libasound2t64 libpulse0 libcups2t64 libatk1.0-0t64 libatk-bridge2.0-0t64 \
        libatspi2.0-0t64 libcairo2 libpango-1.0-0 libgbm1 \
        fonts-wqy-zenhei fonts-wqy-microhei fontconfig \
        dbus-x11 procps \
    && rm -rf /var/lib/apt/lists/*

# fc-cache 先建好，避免微信启动时才扫字体
RUN fc-cache -f >/dev/null 2>&1 || true

# 无障碍 / 抓图解码 / 输入注入 / 进程检查 所需的工具与库
RUN apt-get update && apt-get install -y --no-install-recommends \
        x11-utils x11-xserver-utils xauth xclip \
        dbus at-spi2-core python3 python3-pyatspi \
        libglib2.0-bin gsettings-desktop-schemas \
        python3-pil python3-xlib zbar-tools \
        libatomic1 \
        libgtk-3-0t64 libgdk-pixbuf-2.0-0 libsecret-1-0 libgles2 \
        libwayland-egl1 libwayland-client0 libwayland-cursor0 \
        libx11-xcb1 libxcb-dri3-0 libxcb-present0 libxcb-sync1 \
        libxcb-glx0 libxcb-shm0 libxcb-util1 \
        libsm6 libice6 util-linux curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 强制 GTK3 打开无障碍桥。
# 注意两点：
#  1. 必须先有 libglib2.0-bin 提供 glib-compile-schemas，否则编译静默失败、override 形同虚设；
#  2. 正确的键是 org.gnome.desktop.interface toolkit-accessibility（默认 false），
#     没有 gtk-modules 这个键。
RUN printf "[org.gnome.desktop.interface]\ntoolkit-accessibility=true\n\n[org.gnome.desktop.a11y.applications]\nscreen-reader-enabled=true\n" \
        > /usr/share/glib-2.0/schemas/99-wejam-a11y.gschema.override \
    && glib-compile-schemas /usr/share/glib-2.0/schemas \
    && gsettings get org.gnome.desktop.interface toolkit-accessibility

# ---------------------------------------------------------------------------
# 微信本体：从官方 CDN 下载 .deb 安装。
# 不再依赖宿主已安装的微信 —— 这是可移植性的前提。
# 用 apt 安装本地 deb 而不是 dpkg -i，让依赖自动解析。
# ---------------------------------------------------------------------------
RUN set -eux; \
    curl -fL --retry 3 -o /tmp/wechat.deb "$WECHAT_DEB_URL"; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/wechat.deb; \
    rm -f /tmp/wechat.deb; \
    rm -rf /var/lib/apt/lists/*; \
    dpkg-query -W -f='${Package} ${Version}\n' wechat; \
    test -x /opt/wechat/wechat

# ---------------------------------------------------------------------------
# Agent 运行时：grpcio + protobuf 用 venv，但必须 --system-site-packages，
# 否则看不到系统装的 pyatspi/gi。
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/wejam/venv \
    && /opt/wejam/venv/bin/pip install -q \
         -i https://pypi.tuna.tsinghua.edu.cn/simple \
         grpcio protobuf \
    && /opt/wejam/venv/bin/python -c "import grpc, pyatspi; print('agent runtime ok')"

# Agent 源码 + 共用 stub
COPY agent/ /opt/wejam/agent/
COPY wejam/ /opt/wejam/wejam/
# COPY 会保留宿主权限位；统一放开读权限，否则非 root 的 wx 用户读不到
RUN chmod -R a+rX /opt/wejam

# 非 root 用户跑微信
RUN useradd -m -u 1000 -s /bin/bash wx

# 状态目录：由命名卷挂载进来（entrypoint 会在首次启动时初始化）
VOLUME ["/data"]

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

ENV DISPLAY=:77 \
    HOME=/data/home \
    XDG_RUNTIME_DIR=/data/run \
    WEJAM_DATA=/data \
    XMODIFIERS=@im=none \
    QT_IM_MODULE= \
    GTK_IM_MODULE= \
    QT_IM_MODULES= \
    LANG=zh_CN.UTF-8

ENTRYPOINT ["/entrypoint.sh"]
