#!/usr/bin/env bash
# 以「工作节点」身份加入集群（macOS/Linux）。协调端地址/令牌经环境变量传入，
# 本机可跑的模型来自本仓库的 models.yaml（请按本机情况配置 device/python 等）。
set -euo pipefail
cd "$(dirname "$0")/.."

export VG_CLUSTER_ROLE=agent
export VG_NODE_ID="${VG_NODE_ID:?请设置 VG_NODE_ID，如 node-2}"
export VG_NODE_NAME="${VG_NODE_NAME:-$VG_NODE_ID}"
export VG_COORDINATOR_URL="${VG_COORDINATOR_URL:?请设置协调端地址，如 http://mac-main:8080}"
export VG_CLUSTER_TOKEN="${VG_CLUSTER_TOKEN:-}"

echo ">> agent ${VG_NODE_ID} → ${VG_COORDINATOR_URL}"
exec conda run --no-capture-output -n vg-gateway python -m gateway.agent
