param(
    [string]$PluginRepo = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'dsh-shanghaitech-genai'),
    [string]$LocalRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ShanghaiTech-GenAI'),
    [string]$DshDir = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs/DeepSeek Harness'),
    [string]$GatewayUrl,
    [string]$GatewayKeyFile,
    [switch]$SkipPlugin
)
$ErrorActionPreference = 'Stop'
foreach ($command in @('git', 'docker')) { if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "$command is required." } }
if (-not (Test-Path -LiteralPath (Join-Path $DshDir 'resources/runtime/cli/bin/dsh.cmd'))) { throw 'DSH installation missing; specify -DshDir.' }
if (-not (Test-Path -LiteralPath (Join-Path $PluginRepo 'package.json'))) { throw 'Clone dsh-shanghaitech-genai next to the gateway repository, or specify -PluginRepo.' }
& docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop first.' }
& (Join-Path $PSScriptRoot 'Sync-Local.ps1') -PluginRepo $PluginRepo -LocalRoot $LocalRoot -DshDir $DshDir
$deploy = Join-Path $LocalRoot 'deploy'
$private = Join-Path $deploy 'private'
if ($PSBoundParameters.ContainsKey('GatewayUrl')) {
    if ($GatewayKeyFile) {
        $source = (Resolve-Path -LiteralPath $GatewayKeyFile).Path
        $dest = Join-Path $private 'remote-gateway.key'
        if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($dest)) { Copy-Item -LiteralPath $source -Destination $dest -Force }
    }
    @{ base_url = $GatewayUrl; key_file = 'remote-gateway.key' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $private 'dsh-gateway.json') -Encoding utf8
} elseif ($GatewayKeyFile) { throw '-GatewayKeyFile requires -GatewayUrl.' }
. (Join-Path $PSScriptRoot 'Gateway-Client.ps1')
$gateway = Get-DshGateway $deploy
$key = [IO.File]::ReadAllText($gateway.KeyFile).Trim()
if ($key.Length -lt 32 -or $key.Length -gt 4096 -or $key -match '[^\x21-\x7e]') { throw 'Invalid gateway key file.' }
$key = $null
& (Join-Path $PSScriptRoot 'Deploy-Local.ps1') -LocalRoot $LocalRoot -DshDir $DshDir -SkipPlugin:$SkipPlugin
Write-Host "Runtime directory: $LocalRoot"
Write-Host ('Fully quit DSH, then run: ' + (Join-Path $deploy 'Start.ps1'))
