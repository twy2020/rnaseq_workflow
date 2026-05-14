$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow container restart"
if (-not (Test-DockerReady)) { exit 1 }

$ok = Invoke-Step "Stop container" { Stop-ToolsContainer }
$ok = (Invoke-Step "Start container" { Start-ToolsContainer }) -and $ok
Show-DockerSummary
$testsOk = Invoke-ContainerSmokeTests

if ($ok -and $testsOk) {
    Write-Ok "Container restarted"
    exit 0
}
Write-Fail "Container restart completed with failures"
exit 1
