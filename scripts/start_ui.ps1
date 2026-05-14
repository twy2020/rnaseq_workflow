$ErrorActionPreference = "Stop"
. "$PSScriptRoot/docker_common.ps1"

Write-Section "RNA-seq workflow UI startup"
if (-not (Test-DockerReady)) { exit 1 }

$imageOk = Test-ToolsImage
if (-not $imageOk) {
    Write-Warn "Image is missing. Building incrementally first."
    if (-not (Invoke-Step "Build image with cache" { Build-ToolsImage })) { exit 1 }
}

if (-not (Invoke-Step "Start container" { Start-ToolsContainer })) { exit 1 }
Show-DockerSummary
if (-not (Invoke-ContainerSmokeTests)) { exit 1 }

Write-Section "Launch terminal UI"
Set-Location $Script:ProjectRoot
$env:PYTHONPATH = "workflow"
python -m rnaseq_workflow.cli.main ui
