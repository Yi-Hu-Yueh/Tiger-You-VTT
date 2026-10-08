$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$directory = Join-Path $repo '.runtime/standalone-deps'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$target = Join-Path $directory 'sherpa-onnx-1.13.8.aar'
$expected = '633C24321E06B1FE79FEAFA03EA16CBC0F8A286641E2DA3559BAC91BDB13BD96'
if (-not (Test-Path -LiteralPath $target)) {
    Invoke-WebRequest 'https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar' -OutFile $target
}
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) {
    throw 'Runtime checksum mismatch. Inspect the downloaded file; it was not deleted.'
}
Write-Output 'Pinned Android runtime verified. No model downloaded.'
