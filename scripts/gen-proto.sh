#!/bin/bash
# 从 proto 重新生成 gRPC 绑定。
#
# 输出目录用仓库根：protoc 会镜像 proto 的目录结构，
# 于是 proto/wejam/v1/wejam.proto -> wejam/v1/wejam_pb2.py。
# 这样「proto 包名 wejam.v1」与「Python 子包 wejam.v1」严格对应，
# 客户端与服务端共用同一份，不需要任何 sys.path 注入。
#
# 生成的 *_pb2*.py **入库**（否则从干净克隆 pip install 会因缺少
# wejam.v1 而失败，用户还得先装 protoc）。代价是可能与 proto 脱节，
# 所以提供 --check 做一致性校验，CI/提交前跑一下即可。
#
#   bash scripts/gen-proto.sh          重新生成
#   bash scripts/gen-proto.sh --check  只校验是否与 proto 同步
set -eu
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
OUT_SERVER="wejam/v1/wejam_pb2.py"
OUT_CLIENT="wejam/v1/wejam_pb2_grpc.py"

generate_into() {
    local dest="$1"
    mkdir -p "$dest/wejam/v1"
    "$PYTHON" -m grpc_tools.protoc \
        -I proto \
        --python_out="$dest" \
        --grpc_python_out="$dest" \
        proto/wejam/v1/wejam.proto
    touch "$dest/wejam/v1/__init__.py"
}

if [ "${1:-}" = "--check" ]; then
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    generate_into "$tmp" >/dev/null
    status=0
    for f in "$OUT_SERVER" "$OUT_CLIENT"; do
        if ! diff -q "$f" "$tmp/$f" >/dev/null 2>&1; then
            echo "❌ $f 与 proto 不同步"
            diff -u "$f" "$tmp/$f" | head -20 || true
            status=1
        fi
    done
    if [ "$status" -eq 0 ]; then
        echo "✅ 生成的 stub 与 proto 同步"
    else
        echo "提示：执行 bash scripts/gen-proto.sh 重新生成"
    fi
    exit "$status"
fi

generate_into .
echo "已生成 $OUT_SERVER 与 $OUT_CLIENT"
