$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow container start"
if (-not (Test-DockerReady)) { exit 1 }

$imageOk = Test-ToolsImage
if (-not $imageOk) {
    Write-Warn "Image is missing. Building incrementally first."
    if (-not (Invoke-Step "Build image with cache" { Build-ToolsImage })) { exit 1 }
}

$ok = Invoke-Step "Start container" { Start-ToolsContainer }
Show-DockerSummary
$testsOk = Invoke-ContainerSmokeTests

if ($ok -and $testsOk) {
    Write-Ok "Container is ready"
    exit 0
}
Write-Fail "Container start completed with failures"
exit 1
