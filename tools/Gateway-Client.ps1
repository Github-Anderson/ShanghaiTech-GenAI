function Get-DshGateway([string]$Deploy) {
    $private = Join-Path $Deploy 'private'
    $file = Join-Path $private 'dsh-gateway.json'
    $config = @{}
    if (Test-Path -LiteralPath $file) { $config = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json }
    $remote = -not [string]::IsNullOrWhiteSpace($config.base_url)
    $base = 'http://127.0.0.1:8000/v1'
    $name = 'gateway.key'
    if ($remote) {
        $uri = $null
        if (-not [Uri]::TryCreate($config.base_url.Trim(), [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http', 'https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { throw 'Invalid private/dsh-gateway.json base_url.' }
        $base = $uri.AbsoluteUri.TrimEnd('/')
        if (-not $base.EndsWith('/v1')) { $base += '/v1' }
        $name = $config.key_file
        if (-not $name -or [IO.Path]::IsPathRooted($name) -or ($name -split '[\\/]') -contains '..') { throw 'Invalid private/dsh-gateway.json key_file.' }
    }
    return @{ BaseUrl = $base; RootUrl = $base.Substring(0, $base.Length - 3); KeyFile = (Join-Path $private $name); Remote = $remote }
}
