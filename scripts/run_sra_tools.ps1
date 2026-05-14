param(
    [string[]]$Command = @("fasterq-dump", "--version"),
    [string]$Image = "rnaseq-workflow:sra-tools",
    [string]$Workspace = "."
)

$ErrorActionPreference = "Stop"

$resolvedWorkspace = Resolve-Path $Workspace
docker run --rm `
    -v "${resolvedWorkspace}:/workspace" `
    -w /workspace `
    $Image `
    @Command
