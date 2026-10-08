param([string]$CacheDirectory = 'D:\TigerModels\Tiger-You-VTT\SenseVoice-Small-INT8')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../scripts/prepare_standalone_model.ps1') -CacheDirectory $CacheDirectory

function Assert-Rejected([scriptblock]$Action) {
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    if (!$rejected) { throw 'Expected rejection did not occur.' }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('tiger-model-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    $before = @{}
    foreach ($entry in Get-StandaloneModelManifest) {
        $path = Join-Path $CacheDirectory $entry.Name
        Assert-StandaloneAsset $path $entry.Size $entry.Hash
        $before[$entry.Name] = (Get-Item -LiteralPath $path).LastWriteTimeUtc.Ticks
    }
    Write-Output 'PASS valid cached assets'
    $fixture = Join-Path $testRoot 'fixture.bin'
    [IO.File]::WriteAllBytes($fixture, [byte[]](1,2,3))
    $fixtureHash = (Get-FileHash -LiteralPath $fixture).Hash
    Assert-Rejected { Assert-StandaloneAsset $fixture 4 $fixtureHash }
    Write-Output 'PASS wrong size rejected'
    Assert-Rejected { Assert-StandaloneAsset $fixture 3 ('0' * 64) }
    Write-Output 'PASS wrong hash rejected'
    $assets = Join-Path $testRoot 'assets'
    Initialize-StandaloneModel -Cache $CacheDirectory -Assets $assets -Offline | Out-Null
    $copiedTimes = @{}
    foreach ($entry in Get-StandaloneModelManifest) {
        $path = Join-Path $assets $entry.Name
        Assert-StandaloneAsset $path $entry.Size $entry.Hash
        $copiedTimes[$entry.Name] = (Get-Item -LiteralPath $path).LastWriteTimeUtc.Ticks
    }
    Write-Output 'PASS copied assets verified'
    Initialize-StandaloneModel -Cache $CacheDirectory -Assets $assets -Offline | Out-Null
    foreach ($entry in Get-StandaloneModelManifest) {
        if ((Get-Item -LiteralPath (Join-Path $assets $entry.Name)).LastWriteTimeUtc.Ticks -ne $copiedTimes[$entry.Name]) { throw 'Idempotence failure.' }
        $path = Join-Path $CacheDirectory $entry.Name
        Assert-StandaloneAsset $path $entry.Size $entry.Hash
        if ((Get-Item -LiteralPath $path).LastWriteTimeUtc.Ticks -ne $before[$entry.Name]) { throw 'Authoritative cache changed.' }
    }
    Write-Output 'PASS idempotent rerun'
    Write-Output 'PASS authoritative cache preserved'
    $badCache = Join-Path $testRoot 'partial-cache'
    New-Item -ItemType Directory -Path $badCache | Out-Null
    Copy-Item -LiteralPath $fixture -Destination (Join-Path $badCache 'model.int8.onnx.part')
    Assert-Rejected { Initialize-StandaloneModel -Cache $badCache -Assets (Join-Path $testRoot 'rejected-assets') -Offline }
    if (Test-Path -LiteralPath (Join-Path $testRoot 'rejected-assets/model.int8.onnx')) { throw 'Partial accepted.' }
    Write-Output 'PASS partial not accepted'
} finally {
    $resolvedTest = [IO.Path]::GetFullPath($testRoot)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (!$resolvedTest.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolvedTest -Leaf) -notlike 'tiger-model-test-*') { throw 'Unsafe cleanup target.' }
    Remove-Item -LiteralPath $resolvedTest -Recurse -Force
}
