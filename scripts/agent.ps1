# 以「工作节点」身份加入集群（Windows）。在仓库根目录运行：
#   conda activate vg-gateway ; ./scripts/agent.ps1
# 先按本机情况配置 models.yaml（cosyvoice3/f5 的 python 路径、device: cuda、权重目录）。
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$env:VG_CLUSTER_ROLE    = "agent"
$env:VG_NODE_ID         = "win-4060"
$env:VG_NODE_NAME       = "Windows 4060"
$env:VG_COORDINATOR_URL = "http://mac-main:8080"   # 改成协调端的 Tailscale/局域网地址
$env:VG_CLUSTER_TOKEN   = ""                         # 与协调端一致

Write-Host ">> agent $($env:VG_NODE_ID) -> $($env:VG_COORDINATOR_URL)"
conda run --no-capture-output -n vg-gateway python -m gateway.agent
