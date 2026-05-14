param(
    [string[]]$Command = @("fastqc", "--version"),
    [string]$Image = "rnaseq-workflow:tools",
    [string]$Workspace = "."
)

$ErrorActionPreference = "Stop"

$resolvedWorkspace = Resolve-Path $Workspace
docker run --rm `
    -v "${resolvedWorkspace}:/workspace" `
    -w /workspace `
    $Image `
    @Command
