param(
    [ValidateSet("Auto", "Executable", "Python")]
    [string]$Mode = "Auto"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$valueName = "VoiceGeneration"

function Find-GatewayPythonW {
    if ($env:VG_GATEWAY_PYTHON) {
        $pythonW = Join-Path (Split-Path $env:VG_GATEWAY_PYTHON -Parent) "pythonw.exe"
        if (Test-Path -LiteralPath $pythonW) { return (Resolve-Path -LiteralPath $pythonW).Path }
    }

    $condaCandidates = @(
        $env:CONDA_EXE,
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "D:\Users\$env:USERNAME\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCommand) { $condaCandidates += $condaCommand.Source }

    foreach ($conda in $condaCandidates | Select-Object -Unique) {
        $condaBase = (& $conda info --base 2>$null | Select-Object -First 1).Trim()
        if ($condaBase) {
            $candidate = Join-Path $condaBase "envs\vg-gateway\pythonw.exe"
            if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
        }
        $envList = & $conda env list --json 2>$null | ConvertFrom-Json
        foreach ($envPath in $envList.envs) {
            if ((Split-Path $envPath -Leaf) -eq "vg-gateway") {
                $candidate = Join-Path $envPath "pythonw.exe"
                if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
            }
        }
    }
    throw "vg-gateway pythonw.exe was not found. Create the environment or set VG_GATEWAY_PYTHON."
}

$releaseExe = Join-Path $repo "dist\VoiceGeneration.exe"
if ($Mode -eq "Auto") { $Mode = if (Test-Path -LiteralPath $releaseExe) { "Executable" } else { "Python" } }

if ($Mode -eq "Executable") {
    if (-not (Test-Path -LiteralPath $releaseExe)) { throw "Release executable does not exist: $releaseExe" }
    $command = '"{0}"' -f (Resolve-Path -LiteralPath $releaseExe).Path
} else {
    $pythonW = Find-GatewayPythonW
    $trayScript = (Resolve-Path -LiteralPath (Join-Path $repo "scripts\tray.py")).Path
    $command = '"{0}" "{1}"' -f $pythonW, $trayScript
}

New-Item -Path $runKey -Force | Out-Null
Set-ItemProperty -Path $runKey -Name $valueName -Value $command -Type String
Write-Host "VoiceGeneration logon autostart enabled: $command"
