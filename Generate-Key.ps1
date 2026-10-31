param(
    [string]$DeployDir = $PSScriptRoot,
    [string]$TemplateFile = (Join-Path $DeployDir 'config/container.example.json'),
    [switch]$Copy
)
$ErrorActionPreference = 'Stop'
$privateDir = Join-Path $DeployDir 'private'
$keyPath = Join-Path $privateDir 'gateway.key'
$configPath = Join-Path $privateDir 'container.json'
if (-not (Test-Path -LiteralPath $TemplateFile -PathType Leaf)) { throw 'Configuration template is missing.' }
[void][IO.Directory]::CreateDirectory((Join-Path $privateDir 'browser-credentials'))
if (-not (Test-Path -LiteralPath $configPath)) {
    $config = Get-Content -LiteralPath $TemplateFile -Raw | ConvertFrom-Json
    $config.gateway_key_file = '/run/secrets/gateway_key'
    $config.imported_credentials_file = '/app/browser-credentials/credentials.json'
    $config.site_signing_file = '/app/browser-credentials/site-signing.json'
    $json = $config | ConvertTo-Json -Depth 16
    [IO.File]::WriteAllText($configPath, $json, (New-Object Text.UTF8Encoding($false)))
}
if (-not (Test-Path -LiteralPath $keyPath)) {
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 32
    try {
        $random.GetBytes($bytes)
        $encoded = [Text.Encoding]::UTF8.GetBytes([Convert]::ToBase64String($bytes))
        $stream = [IO.File]::Open($keyPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($encoded, 0, $encoded.Length) } finally { $stream.Dispose() }
    } finally {
        $random.Dispose()
        [Array]::Clear($bytes, 0, $bytes.Length)
        if ($encoded) { [Array]::Clear($encoded, 0, $encoded.Length) }
    }
}
$key = [IO.File]::ReadAllText($keyPath).Trim()
try {
    if ($key.Length -lt 32 -or $key.Length -gt 4096 -or $key -match '[^\x21-\x7e]') {
        throw 'private/gateway.key must contain at least 32 printable ASCII characters without spaces.'
    }
    if ($Copy) {
        Set-Clipboard -Value $key
        Write-Host 'Key copied to clipboard. Paste it into SHANGHAITECH_GENAI_API_KEY in DSH credentials.'
    }
    Write-Host 'Ready: private/container.json, private/gateway.key, private/browser-credentials/.'
} finally { $key = $null }
