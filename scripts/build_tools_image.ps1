param(
    [string]$Tag = "rnaseq-workflow:tools",
    [string]$ProxyEnv = "",
    [switch]$NoHostProxy
)

$ErrorActionPreference = "Stop"

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not $Path -or -not (Test-Path $Path)) {
        return $values
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $key, $value = $line.Split("=", 2)
        $values[$key.Trim()] = $value.Trim()
    }
    return $values
}

$envValues = Read-EnvFile -Path $ProxyEnv

foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "APT_MIRROR", "PIP_INDEX_URL")) {
    if (-not $NoHostProxy -and -not $envValues.ContainsKey($name) -and [Environment]::GetEnvironmentVariable($name)) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value -match "127\.0\.0\.1") {
            $value = $value -replace "127\.0\.0\.1", "host.docker.internal"
        }
        $envValues[$name] = $value
    }
}

$argsList = @("build", "-f", "docker/Dockerfile.tools", "-t", $Tag)
foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "APT_MIRROR", "PIP_INDEX_URL")) {
    if ($envValues.ContainsKey($name) -and $envValues[$name]) {
        $argsList += @("--build-arg", "$name=$($envValues[$name])")
    }
}
$argsList += "."

Write-Host "Building Docker image: $Tag"
docker @argsList
