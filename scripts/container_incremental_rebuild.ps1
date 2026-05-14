$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow container incremental rebuild"
if (-not (Test-DockerReady)) { exit 1 }

$ok = Invoke-Step "Build image with cache" { Build-ToolsImage }
$ok = (Invoke-Step "Recreate container" {
    Stop-ToolsContainer
    Remove-ToolsContainer
    Start-ToolsContainer
}) -and $ok

Show-DockerSummary
$testsOk = Invoke-ContainerSmokeTests

if ($ok -and $testsOk) {
    Write-Ok "Incremental rebuild completed"
    exit 0
}
Write-Fail "Incremental rebuild completed with failures"
exit 1
