$ErrorActionPreference = 'Stop'
$seerRoot = Split-Path -Parent $PSScriptRoot
$seerCompiler = Get-Command g++ -ErrorAction SilentlyContinue
if (-not $seerCompiler) { throw 'Install MinGW-w64 (UCRT64) with current Windows headers and put g++ on PATH.' }
New-Item -ItemType Directory -Path (Join-Path $seerRoot 'bin') -Force | Out-Null
& $seerCompiler.Source '-std=c++17' '-O2' '-static' '-municode' (Join-Path $seerRoot 'native/seer_capture.cpp') '-o' (Join-Path $seerRoot 'bin/seer-capture.exe') '-ld3d11' '-ldxgi' '-lruntimeobject' '-lole32' '-lmmdevapi' '-luuid'
if ($LASTEXITCODE -ne 0) { throw 'Native capture helper build failed.' }
