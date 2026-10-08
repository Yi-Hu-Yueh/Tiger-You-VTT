param([string]$Python = '', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $existingPython = Join-Path (Split-Path -Parent $projectRoot) 'venv_multi_query\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $existingPython) { $existingPython } else { 'python' }
}
$runtime = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$tokenFile = Join-Path $runtime 'mobile_api_token.txt'
if (-not (Test-Path -LiteralPath $tokenFile)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [Convert]::ToBase64String($bytes)
    # CreateNew prevents concurrent launchers from overwriting an existing token.
    $stream = [IO.File]::Open($tokenFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $encoded = [Text.Encoding]::UTF8.GetBytes($token)
        $stream.Write($encoded, 0, $encoded.Length)
    } finally { $stream.Dispose() }
}
$token = [IO.File]::ReadAllText($tokenFile).Trim()
if ($token -notmatch '^[A-Za-z0-9+/=_-]{32,128}$') { throw 'Invalid mobile token file; use a cryptographically random token of at least 32 characters.' }
$previousToken = $env:TIGER_MOBILE_TOKEN
$env:TIGER_MOBILE_TOKEN = $token
Write-Host 'Tiger-You-VTT Mobile Server'
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*'
} | ForEach-Object { Write-Host ('Server URL: http://' + $_.IPAddress + ':' + $Port) }
Write-Host ('Mobile Token: ' + $token)
Write-Host 'Trusted private LAN only. HTTP is not encrypted. Firewall settings are unchanged.'
Push-Location $projectRoot
try {
    # Do not let proxy headers turn a LAN peer into a trusted loopback peer.
    & $Python -m uvicorn app.main:app --host 0.0.0.0 --port $Port --no-proxy-headers
} finally {
    Pop-Location
    $env:TIGER_MOBILE_TOKEN = $previousToken
}
