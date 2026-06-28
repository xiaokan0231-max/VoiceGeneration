#!/usr/bin/env bash
# 以「工作节点」身份加入集群（macOS/Linux）。
# 首次：cp models.example.yaml models.yaml，按本机改 python/device/system。
# 启动后浏览器打开 http://127.0.0.1:8090，在网页里填主节点地址/令牌、设模型副本（保存即热生效）。
# 主节点地址/令牌可在【主机】「服务设置 → 副节点接入信息」复制。
set -euo pipefail
cd "$(dirname "$0")/.."

export VG_CLUSTER_ROLE=agent
# 可选首次引导（之后改用网页配置）：
# export VG_COORDINATOR_URL="http://mac-main:8080"
# export VG_CLUSTER_TOKEN=""

echo ">> 启动副节点 agent；控制台 http://127.0.0.1:8090"
exec conda run --no-capture-output -n vg-gateway python -m gateway.agent
