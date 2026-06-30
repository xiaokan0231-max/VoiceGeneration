$ErrorActionPreference = "Stop"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$valueName = "VoiceGeneration"

if (Get-ItemProperty -Path $runKey -Name $valueName -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name $valueName
    Write-Host "VoiceGeneration logon autostart disabled."
} else {
    Write-Host "VoiceGeneration logon autostart is not enabled."
}
