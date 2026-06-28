# 以「工作节点」身份加入集群（Windows）。在仓库根目录运行：
#   conda activate vg-gateway ; ./scripts/agent.ps1
#
# 首次：Copy-Item models.example.yaml models.yaml，按本机改 python 路径 / device:cuda / system 设 enabled:false。
# 启动后浏览器打开 http://127.0.0.1:8090 —— 在网页里填主节点地址/令牌、设模型副本（保存即热生效）。
# 主节点地址/令牌可在【主机】的「服务设置 → 副节点接入信息」里直接复制。
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# 可选：首次用环境变量引导（之后建议改用网页配置；网页保存会写入 models.yaml）
# $env:VG_COORDINATOR_URL = "http://mac-main:8080"
# $env:VG_CLUSTER_TOKEN   = ""
$env:VG_CLUSTER_ROLE = "agent"

Write-Host ">> 启动副节点 agent；控制台 http://127.0.0.1:8090"
conda run --no-capture-output -n vg-gateway python -m gateway.agent
