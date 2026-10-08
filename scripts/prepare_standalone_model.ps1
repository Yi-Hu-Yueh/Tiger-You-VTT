param(
    [string]$CacheDirectory = 'D:\TigerModels\Tiger-You-VTT\SenseVoice-Small-INT8',
    [string]$AssetDirectory = (Join-Path $PSScriptRoot '../android-standalone/app/src/main/assets/sensevoice-small-int8'),
    [switch]$NoDownload
)

$ErrorActionPreference = 'Stop'

function Get-StandaloneModelManifest {
    $base = 'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07'
    @(
        @{ Name = 'model.int8.onnx'; Size = 239233841L; Hash = 'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51'; Url = "$base/model.int8.onnx" },
        @{ Name = 'tokens.txt'; Size = 315894L; Hash = 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc'; Url = "$base/tokens.txt" }
    )
}

function Assert-StandaloneAsset([string]$Path, [long]$Size, [string]$Hash) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Required model asset missing.' }
    if ((Get-Item -LiteralPath $Path).Length -ne $Size) { throw 'Model asset size mismatch.' }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Hash) { throw 'Model asset SHA256 mismatch.' }
}

function Initialize-StandaloneModel([string]$Cache, [string]$Assets, [switch]$Offline) {
    $cacheRoot = [IO.Path]::GetFullPath($Cache)
    $assetRoot = [IO.Path]::GetFullPath($Assets)
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $assetRoot -Force | Out-Null
    foreach ($entry in Get-StandaloneModelManifest) {
        $cached = Join-Path $cacheRoot $entry.Name
        if (!(Test-Path -LiteralPath $cached)) {
            if ($Offline) { throw 'Verified cache asset missing; downloads disabled.' }
            $part = Join-Path $cacheRoot ($entry.Name + '.' + [guid]::NewGuid().ToString('N') + '.part')
            try {
                # Only pinned public HTTPS URLs; no credentials or HF cache lookup.
                Invoke-WebRequest -Uri $entry.Url -OutFile $part -UseBasicParsing -ErrorAction Stop
                Assert-StandaloneAsset $part $entry.Size $entry.Hash
                # Never overwrite another process's cache entry.
                Move-Item -LiteralPath $part -Destination $cached -ErrorAction Stop
            } catch {
                throw "Model preparation failed for $($entry.Name); no unverified asset was accepted."
            } finally {
                if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
            }
        }
        # A corrupt preexisting cache is preserved and rejected, never silently replaced.
        Assert-StandaloneAsset $cached $entry.Size $entry.Hash
        $asset = Join-Path $assetRoot $entry.Name
        $alreadyValid = $false
        if (Test-Path -LiteralPath $asset -PathType Leaf) {
            try { Assert-StandaloneAsset $asset $entry.Size $entry.Hash; $alreadyValid = $true } catch { }
        }
        if (!$alreadyValid) {
            $copyPart = Join-Path $assetRoot ($entry.Name + '.' + [guid]::NewGuid().ToString('N') + '.part')
            try {
                Copy-Item -LiteralPath $cached -Destination $copyPart
                Assert-StandaloneAsset $copyPart $entry.Size $entry.Hash
                Move-Item -LiteralPath $copyPart -Destination $asset -Force
            } finally {
                if (Test-Path -LiteralPath $copyPart) { Remove-Item -LiteralPath $copyPart -Force }
            }
        }
        Assert-StandaloneAsset $asset $entry.Size $entry.Hash
        Write-Output "Verified $($entry.Name): $($entry.Size) bytes, SHA256 $($entry.Hash)"
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Initialize-StandaloneModel -Cache $CacheDirectory -Assets $AssetDirectory -Offline:$NoDownload
}
