param([string]$DshExe = "$env:LOCALAPPDATA/Programs/DeepSeek Harness/DeepSeek Harness.exe")
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $DshExe -PathType Leaf)) { throw 'DSH executable is missing. Specify -DshExe with its full path.' }
. (Join-Path $PSScriptRoot 'tools/Gateway-Client.ps1')
$gateway = Get-DshGateway $PSScriptRoot
if (-not $gateway.Remote) { & (Join-Path $PSScriptRoot 'Start-Gateway.ps1') }
$previousNodeMode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    & $DshExe --expose-internals (Join-Path $PSScriptRoot 'Sync-Key.cjs') $gateway.KeyFile | Out-String -Stream | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'DSH credential synchronization failed; DSH was not launched.' }
} finally {
    if ($null -eq $previousNodeMode) { Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue }
    else { $env:ELECTRON_RUN_AS_NODE = $previousNodeMode }
}
$env:SHANGHAITECH_GENAI_BASE_URL = $gateway.BaseUrl
$hostName = ([Uri]$gateway.BaseUrl).Host
$env:NO_PROXY = (($env:NO_PROXY, $hostName, '127.0.0.1', 'localhost') | Where-Object { $_ }) -join ','
$env:no_proxy = $env:NO_PROXY
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
Start-Process -FilePath $DshExe -WindowStyle Normal
Start-Process ($gateway.RootUrl + '/')
