$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow container stop"
if (-not (Test-DockerReady)) { exit 1 }

$ok = Invoke-Step "Stop container" { Stop-ToolsContainer }
Show-DockerSummary

if ($ok) {
    Write-Ok "Container stopped"
    exit 0
}
Write-Fail "Container stop completed with failures"
exit 1
