$ErrorActionPreference = "Stop"

$Script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Script:ImageName = "rnaseq-workflow:tools"
$Script:ContainerName = "rnaseq-workflow-tools"
$Script:Dockerfile = "docker/Dockerfile.tools"
$Script:ProxyEnv = "docker/proxy.env"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "== $Title ==" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Invoke-Step {
    param(
        [string]$Title,
        [scriptblock]$Block
    )
    Write-Section $Title
    try {
        & $Block
        Write-Ok $Title
        return $true
    }
    catch {
        Write-Fail "$Title failed: $($_.Exception.Message)"
        return $false
    }
}

function Test-DockerReady {
    Write-Section "Docker daemon"
    try {
        $version = docker version --format "{{.Server.Version}}" 2>$null
        if (-not $version) {
            throw "Docker daemon did not return a server version."
        }
        Write-Ok "Docker daemon is running. Server version: $version"
        return $true
    }
    catch {
        Write-Fail "Docker daemon is not available. Start Docker Desktop and retry."
        return $false
    }
}

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

function Get-BuildArgs {
    $envValues = Read-EnvFile -Path (Join-Path $Script:ProjectRoot $Script:ProxyEnv)
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "APT_MIRROR", "PIP_INDEX_URL")) {
        if (-not $envValues.ContainsKey($name) -and [Environment]::GetEnvironmentVariable($name)) {
            $value = [Environment]::GetEnvironmentVariable($name)
            if ($value -match "127\.0\.0\.1") {
                $value = $value -replace "127\.0\.0\.1", "host.docker.internal"
            }
            $envValues[$name] = $value
        }
    }

    $argsList = @()
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "APT_MIRROR", "PIP_INDEX_URL")) {
        if ($envValues.ContainsKey($name) -and $envValues[$name]) {
            $argsList += @("--build-arg", "$name=$($envValues[$name])")
        }
    }
    return $argsList
}

function Build-ToolsImage {
    param([switch]$NoCache)
    Set-Location $Script:ProjectRoot
    $argsList = @("build", "-f", $Script:Dockerfile, "-t", $Script:ImageName)
    if ($NoCache) {
        $argsList += "--no-cache"
        $argsList += "--pull"
    }
    $argsList += Get-BuildArgs
    $argsList += "."
    Write-Host "docker $($argsList -join ' ')"
    docker @argsList
}

function Remove-ToolsContainer {
    $existing = docker ps -a --filter "name=^/$Script:ContainerName$" --format "{{.Names}}" 2>$null
    if ($existing) {
        docker rm -f $Script:ContainerName | Out-Null
        Write-Ok "Removed container: $Script:ContainerName"
    }
    else {
        Write-Warn "Container not found: $Script:ContainerName"
    }
}

function Start-ToolsContainer {
    Set-Location $Script:ProjectRoot
    $existing = docker ps -a --filter "name=^/$Script:ContainerName$" --format "{{.Names}}" 2>$null
    if ($existing) {
        $running = docker ps --filter "name=^/$Script:ContainerName$" --format "{{.Names}}" 2>$null
        if ($running) {
            Write-Ok "Container already running: $Script:ContainerName"
            return
        }
        docker start $Script:ContainerName | Out-Null
        Write-Ok "Started existing container: $Script:ContainerName"
        return
    }

    $workspace = (Resolve-Path $Script:ProjectRoot).Path
    docker run -d `
        --name $Script:ContainerName `
        -v "${workspace}:/workspace" `
        -w /workspace `
        $Script:ImageName `
        sleep infinity | Out-Null
    Write-Ok "Started new container: $Script:ContainerName"
}

function Stop-ToolsContainer {
    $running = docker ps --filter "name=^/$Script:ContainerName$" --format "{{.Names}}" 2>$null
    if ($running) {
        docker stop $Script:ContainerName | Out-Null
        Write-Ok "Stopped container: $Script:ContainerName"
    }
    else {
        Write-Warn "Container is not running: $Script:ContainerName"
    }
}

function Test-ToolsImage {
    Write-Section "Image status"
    $image = docker image inspect $Script:ImageName --format "{{.Id}} {{.Size}}" 2>$null
    if ($image) {
        Write-Ok "Image exists: $Script:ImageName"
        Write-Host $image
    }
    else {
        Write-Fail "Image missing: $Script:ImageName"
        return $false
    }
    return $true
}

function Test-ToolsContainer {
    Write-Section "Container status"
    docker ps -a --filter "name=^/$Script:ContainerName$" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"
}

function Invoke-ToolCheck {
    param(
        [string]$Name,
        [string[]]$Command
    )
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = & docker exec $Script:ContainerName @Command 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if ($exitCode -eq 0) {
            Write-Ok "$Name"
            if ($output) {
                ($output | Where-Object { "$_" -ne "System.Management.Automation.RemoteException" } | Select-Object -First 2) |
                    ForEach-Object { Write-Host "  $_" }
            }
            return $true
        }
        Write-Fail "$Name exited with code $exitCode"
        if ($output) { Write-Host $output }
        return $false
    }
    catch {
        if ($previousErrorActionPreference) {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        Write-Fail "$Name failed: $($_.Exception.Message)"
        return $false
    }
}

function Invoke-ContainerSmokeTests {
    Write-Section "Container smoke tests"
    $checks = @()
    $checks += Invoke-ToolCheck -Name "python3" -Command @("python3", "--version")
    $checks += Invoke-ToolCheck -Name "prefetch" -Command @("prefetch", "--version")
    $checks += Invoke-ToolCheck -Name "fasterq-dump" -Command @("fasterq-dump", "--version")
    $checks += Invoke-ToolCheck -Name "fastqc" -Command @("fastqc", "--version")
    $checks += Invoke-ToolCheck -Name "trim_galore" -Command @("trim_galore", "--version")
    $checks += Invoke-ToolCheck -Name "hisat2" -Command @("hisat2", "--version")
    $checks += Invoke-ToolCheck -Name "samtools" -Command @("samtools", "--version")
    $checks += Invoke-ToolCheck -Name "featureCounts" -Command @("featureCounts", "-v")

    Write-Section "Smoke test summary"
    $passed = ($checks | Where-Object { $_ }).Count
    $total = $checks.Count
    if ($passed -eq $total) {
        Write-Ok "$passed/$total checks passed"
        return $true
    }
    Write-Fail "$passed/$total checks passed"
    return $false
}

function Show-DockerSummary {
    Test-ToolsImage | Out-Null
    Test-ToolsContainer
}
