param([string]$ComposeFile = (Join-Path $PSScriptRoot 'compose.yaml'))
$ErrorActionPreference = 'Stop'
$ComposeFile = (Resolve-Path -LiteralPath $ComposeFile).Path
Push-Location (Split-Path -Parent $ComposeFile)
try {
    foreach ($name in @('private/container.json', 'private/gateway.key', 'private/browser-credentials')) {
        if (-not (Test-Path -LiteralPath $name)) { throw 'Run Generate-Key.ps1 before starting the gateway.' }
    }
    $compose = @('compose', '-f', $ComposeFile, '-p', 'shanghaitech-genai')
    & docker @compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration validation failed.' }
    & docker @compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'Gateway startup failed. Check Docker Desktop and the terminal error.' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 2
            if ($health.product -eq 'ShanghaiTech GenAI' -and $health.version -eq '1.0.1') { $ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Gateway did not become ready. Check docker compose logs.' }
    Write-Host 'ShanghaiTech GenAI v1.0.1: http://127.0.0.1:8000/'
} finally { Pop-Location }
