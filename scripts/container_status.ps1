$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow container status"
if (-not (Test-DockerReady)) { exit 1 }

Show-DockerSummary
$running = docker ps --filter "name=^/$Script:ContainerName$" --format "{{.Names}}" 2>$null
if ($running) {
    Invoke-ContainerSmokeTests | Out-Null
}
else {
    Write-Warn "Container is not running, smoke tests skipped."
}
