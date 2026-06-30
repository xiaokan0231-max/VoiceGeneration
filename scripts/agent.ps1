# Start a Windows cluster worker from the repository root.
# The local control panel is available at http://127.0.0.1:8090.
# Coordinator settings and model replicas can be changed from that page.
param(
    [string]$CoordinatorUrl = $env:VG_COORDINATOR_URL,
    [string]$ClusterToken = $env:VG_CLUSTER_TOKEN,
    [string]$NodeId = $env:VG_NODE_ID,
    [string]$NodeName = $env:VG_NODE_NAME
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$env:VG_CLUSTER_ROLE = "agent"
if ($CoordinatorUrl) { $env:VG_COORDINATOR_URL = $CoordinatorUrl }
if ($ClusterToken) { $env:VG_CLUSTER_TOKEN = $ClusterToken }
if ($NodeId) { $env:VG_NODE_ID = $NodeId }
if ($NodeName) { $env:VG_NODE_NAME = $NodeName }

$python = $env:VG_GATEWAY_PYTHON
if (-not $python) {
    $condaCandidates = @(
        $env:CONDA_EXE,
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "D:\Users\$env:USERNAME\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    $conda = $condaCandidates | Select-Object -First 1
    if (-not $conda) {
        $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
        if ($condaCommand) { $conda = $condaCommand.Source }
    }
    if (-not $conda) { throw "Conda was not found. Set VG_GATEWAY_PYTHON to the gateway Python executable." }
    $condaBase = (& $conda info --base).Trim()
    $python = Join-Path $condaBase "envs\vg-gateway\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $envList = & $conda env list --json | ConvertFrom-Json
        $gatewayEnv = $envList.envs | Where-Object { (Split-Path $_ -Leaf) -eq "vg-gateway" } |
            Select-Object -First 1
        if ($gatewayEnv) { $python = Join-Path $gatewayEnv "python.exe" }
    }
}
if (-not (Test-Path $python)) { throw "Gateway Python does not exist: $python" }

Write-Host ">> Starting agent; control panel: http://127.0.0.1:8090"
& $python -m gateway.agent
