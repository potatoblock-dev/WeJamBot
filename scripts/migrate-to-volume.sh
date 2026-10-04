#!/bin/bash
# 把旧版「挂在代码目录」的状态迁移到命名卷。
#
# 旧布局：HOME=$ROOT/wxhome，machine-id 为 $ROOT/docker/machine-id
# 新布局：命名卷 wejam-data 里的 /data/home 与 /data/machine-id
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VOLUME="${WEJAM_VOLUME:-wejam-data}"
IMAGE="${WEJAM_IMAGE:-wejam/wechat:latest}"

if [ ! -d "$ROOT/wxhome" ]; then
    echo "没有找到 $ROOT/wxhome，无需迁移"
    exit 0
fi

docker volume create "$VOLUME" >/dev/null

echo "迁移 $ROOT/wxhome -> 卷 $VOLUME:/data/home"
docker run --rm \
    -v "$VOLUME:/data" \
    -v "$ROOT/wxhome:/old:ro" \
    -v "$ROOT/docker/machine-id:/mid:ro" \
    --entrypoint bash "$IMAGE" -c '
        mkdir -p /data/home
        cp -a /old/. /data/home/ 2>/dev/null || true
        if [ -s /mid ]; then cp /mid /data/machine-id; fi
        chown -R 1000:1000 /data
        echo "  已复制 $(find /data/home -type f | wc -l) 个文件"
        echo "  machine-id: $(cat /data/machine-id 2>/dev/null || echo 无)"
    '

echo "迁移完成。"
