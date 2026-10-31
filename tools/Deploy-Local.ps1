param(
    [string]$LocalRoot = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'local'),
    [string]$DshDir = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs/DeepSeek Harness'),
    [switch]$SkipPlugin
)
$ErrorActionPreference = 'Stop'
$LocalRoot = (Resolve-Path -LiteralPath $LocalRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $LocalRoot '.genai-local'))) { throw 'Not a managed local environment.' }
$state = Get-Content -LiteralPath (Join-Path $LocalRoot 'sync-state.json') -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $state.package -PathType Leaf)) { throw 'Run Sync-Local.ps1 first.' }
$deploy = Join-Path $LocalRoot 'deploy'
if (-not $SkipPlugin) {
    $cli = Join-Path $DshDir 'resources/runtime/cli/bin/dsh.cmd'
    if (-not (Test-Path -LiteralPath $cli)) { throw 'DSH CLI missing; specify -DshDir.' }
    [void][IO.Directory]::CreateDirectory((Join-Path $LocalRoot 'dsh-backups'))
    # Preserve the desktop profile metadata before switching its dependency to the local package.
    $profile = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dsh/profiles/desktop'
    if ($env:DSH_HOME) { $profile = Join-Path $env:DSH_HOME 'profiles/desktop' }
    $backup = Join-Path $LocalRoot ('dsh-backups/' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($backup)
    foreach ($name in @('package.json', 'pnpm-lock.yaml', 'cordis.patch.yml', 'cordis.yml')) {
        $source = Join-Path $profile $name
        if (Test-Path -LiteralPath $source -PathType Leaf) { Copy-Item -LiteralPath $source -Destination $backup }
    }
    & $cli plugin --profile desktop add $state.package --offline
    if ($LASTEXITCODE -ne 0) { throw 'Local plugin installation failed; gateway was not changed.' }
}
# This local deployment replaces the existing single-instance gateway on port 8000.
& (Join-Path $deploy 'Start-Gateway.ps1') -ComposeFile (Join-Path $deploy 'compose.yaml')
$previous = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    $logs = Join-Path $LocalRoot 'logs'
    [void][IO.Directory]::CreateDirectory($logs)
    $arguments = '--expose-internals "' + (Join-Path $deploy 'Sync-Key.cjs') + '" "' + (Join-Path $deploy 'private/gateway.key') + '"'
    $process = Start-Process -FilePath (Join-Path $DshDir 'DeepSeek Harness.exe') -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $logs 'sync-key.out.log') -RedirectStandardError (Join-Path $logs 'sync-key.err.log')
    if ($process.ExitCode -ne 0) { throw 'DSH credential synchronization failed; see local logs (no key values).' }
} finally {
    if ($null -eq $previous) { Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue } else { $env:ELECTRON_RUN_AS_NODE = $previous }
}
$key = [IO.File]::ReadAllText((Join-Path $deploy 'private/gateway.key')).Trim()
try {
    $headers = @{ Authorization = 'Bearer ' + $key }
    $models = Invoke-RestMethod 'http://127.0.0.1:8000/v1/models' -Headers $headers
    $credentials = Invoke-RestMethod 'http://127.0.0.1:8000/v1/credentials/status' -Headers $headers
    Write-Host ('Authenticated model listing OK: ' + $models.data.Count + ' models.')
    Write-Host ('Website credentials present: ' + $credentials.logged_in + '; upstream not validated.')
} finally { $key = $null; $headers = $null }
Write-Host 'Local deployment complete. Fully restart DSH to load the updated plugin.'
