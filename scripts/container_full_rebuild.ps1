$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow container full rebuild"
if (-not (Test-DockerReady)) { exit 1 }

$ok = Invoke-Step "Stop old container" { Stop-ToolsContainer }
$ok = (Invoke-Step "Remove old container" { Remove-ToolsContainer }) -and $ok
$ok = (Invoke-Step "Remove old image" {
    $image = docker image inspect $Script:ImageName --format "{{.Id}}" 2>$null
    if ($image) {
        docker rmi -f $Script:ImageName | Out-Null
        Write-Ok "Removed image: $Script:ImageName"
    }
    else {
        Write-Warn "Image not found: $Script:ImageName"
    }
}) -and $ok
$ok = (Invoke-Step "Build image without cache" { Build-ToolsImage -NoCache }) -and $ok
$ok = (Invoke-Step "Start container" { Start-ToolsContainer }) -and $ok

Show-DockerSummary
$testsOk = Invoke-ContainerSmokeTests

if ($ok -and $testsOk) {
    Write-Ok "Full rebuild completed"
    exit 0
}
Write-Fail "Full rebuild completed with failures"
exit 1
