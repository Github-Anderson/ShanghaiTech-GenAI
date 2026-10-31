param(
    [string]$PluginRepo = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'dsh-shanghaitech-genai'),
    [string]$LocalRoot = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'local'),
    [string]$PrivateSource,
    [string]$DshDir = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs/DeepSeek Harness')
)
$ErrorActionPreference = 'Stop'
$gatewayRepo = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$PluginRepo = (Resolve-Path -LiteralPath $PluginRepo).Path
$LocalRoot = [IO.Path]::GetFullPath($LocalRoot)
foreach ($repo in @($gatewayRepo, $PluginRepo)) {
    if ($LocalRoot -eq $repo -or $LocalRoot.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'LocalRoot must be outside both source repositories.'
    }
}
$node = Join-Path $DshDir 'resources/runtime/primary-runtime/dependencies/node/bin/node.exe'
$pnpm = Join-Path $DshDir 'resources/runtime/primary-runtime/dependencies/pnpm/bin/pnpm.cjs'
foreach ($file in @($node, $pnpm)) { if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'DSH bundled Node/pnpm missing; specify -DshDir.' } }
[void][IO.Directory]::CreateDirectory($LocalRoot)
$marker = Join-Path $LocalRoot '.genai-local'
if (-not (Test-Path -LiteralPath $marker)) {
    if (@(Get-ChildItem -LiteralPath $LocalRoot -Force).Count) { throw 'Refusing to manage a nonempty unmarked directory.' }
    Set-Content -LiteralPath $marker -Value 'ShanghaiTech GenAI local environment v1' -Encoding utf8
}
# A failed sync must never leave the previous snapshot marked deployable.
$statePath = Join-Path $LocalRoot 'sync-state.json'
if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
function Safe-Target([string]$root, [string]$relative) {
    $base = [IO.Path]::GetFullPath($root).TrimEnd('\', '/')
    $target = [IO.Path]::GetFullPath((Join-Path $base $relative))
    if (-not $target.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe manifest path.' }
    $cursor = $target
    while ($cursor -and $cursor.Length -ge $base.Length) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Links are not allowed in managed paths.' }
        $cursor = Split-Path $cursor -Parent
    }
    return $target
}
function Sync-Public([string]$repo, [string]$destination, [string]$manifestName, [string]$allow) {
    [void][IO.Directory]::CreateDirectory($destination)
    $manifest = Join-Path $LocalRoot $manifestName
    $files = @(& git -c core.quotepath=false -C $repo ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate repository files.' }
    $files = @($files | Sort-Object -Unique | Where-Object {
        $_ -match $allow -and $_ -notmatch '(^|/)(private|browser-credentials|node_modules|__pycache__|\.git)(/|$)' -and
        $_ -notmatch '(^|/)(\.env.*|local\.json|credentials\.json|site-signing\.json)$|\.(key|pem|har|pyc|log)$' -and
        (Test-Path -LiteralPath (Join-Path $repo $_) -PathType Leaf)
    })
    foreach ($relative in $files) {
        $target = Safe-Target $destination $relative
        $source = Safe-Target $repo $relative
        [void][IO.Directory]::CreateDirectory((Split-Path $target -Parent))
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
    if (Test-Path -LiteralPath $manifest) {
        foreach ($relative in @(Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json)) {
            if ($relative -notmatch $allow -or $relative -match '(^|/)private(/|$)') { throw 'Invalid previous manifest.' }
            if ($relative -notin $files) {
                $target = Safe-Target $destination $relative
                if (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target -Force }
            }
        }
    }
    ConvertTo-Json -InputObject @($files) | Set-Content -LiteralPath $manifest -Encoding utf8
}
$deploy = Join-Path $LocalRoot 'deploy'
$plugin = Join-Path $LocalRoot 'sources/plugin'
Sync-Public $gatewayRepo $deploy 'gateway-files.json' '^(gateway/|config/container\.example\.json$|userscript/|tools/|[^/]+\.(ps1|cjs|md)$|compose\.yaml$|LICENSE$)'
Sync-Public $PluginRepo $plugin 'plugin-files.json' '^([^/]+\.(cjs|js|json|yml|svg|md)$|locale/[^/]+\.json$|LICENSE$)'
$private = Join-Path $deploy 'private'
if ($PrivateSource) {
    $PrivateSource = (Resolve-Path -LiteralPath $PrivateSource).Path
    # Only migrate known private files. Never overwrite existing local credentials/configuration.
    foreach ($relative in @('gateway.key', 'container.json', 'browser-credentials/credentials.json', 'browser-credentials/site-signing.json')) {
        $source = Join-Path $PrivateSource $relative
        $target = Safe-Target $private $relative
        if ((Test-Path -LiteralPath $source -PathType Leaf) -and -not (Test-Path -LiteralPath $target)) {
            [void][IO.Directory]::CreateDirectory((Split-Path $target -Parent))
            Copy-Item -LiteralPath $source -Destination $target
            if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) { throw 'Private migration verification failed.' }
        }
    }
}
& (Join-Path $deploy 'Generate-Key.ps1') -DeployDir $deploy
$packages = Join-Path $LocalRoot 'packages'
[void][IO.Directory]::CreateDirectory($packages)
# Unique filenames prevent DSH/pnpm reusing a previously cached package of the same version.
$package = Join-Path $packages ('dsh-shanghaitech-genai-local-' + [guid]::NewGuid().ToString('N') + '.tgz')
Push-Location $plugin
try {
    $packResult = & $node $pnpm pack --out $package --json
    if ($LASTEXITCODE -ne 0) { throw 'Plugin packaging failed.' }
    if (-not (Test-Path -LiteralPath $package -PathType Leaf)) { throw 'Plugin package is missing.' }
} finally { Pop-Location }
function Revision([string]$repo) {
    $commit = & git -C $repo rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read source revision.' }
    $dirty = @(& git -C $repo status --porcelain)
    return @{ commit = "$commit"; dirty = ($dirty.Count -gt 0) }
}
$state = @{ synced_at = (Get-Date).ToUniversalTime().ToString('o'); gateway = (Revision $gatewayRepo); plugin = (Revision $PluginRepo); package = $package }
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
# Convenience entry point lives only in the local environment; source remains in the repositories.
$syncScript = (Join-Path $gatewayRepo 'tools/Sync-Local.ps1').Replace("'", "''")
$deployScript = (Join-Path $gatewayRepo 'tools/Deploy-Local.ps1').Replace("'", "''")
$pluginLiteral = $PluginRepo.Replace("'", "''")
$localLiteral = $LocalRoot.Replace("'", "''")
$dshLiteral = $DshDir.Replace("'", "''")
@"
`$ErrorActionPreference = 'Stop'
& '$syncScript' -PluginRepo '$pluginLiteral' -LocalRoot '$localLiteral' -DshDir '$dshLiteral'
& '$deployScript' -LocalRoot '$localLiteral' -DshDir '$dshLiteral'
"@ | Set-Content -LiteralPath (Join-Path $LocalRoot 'Update.ps1') -Encoding utf8
Write-Host "Synchronized local environment: $LocalRoot"
Write-Host 'Private files preserved. Run Deploy-Local.ps1 to deploy this snapshot.'
