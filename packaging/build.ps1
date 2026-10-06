<#
.SYNOPSIS
  Build the standalone CodeCompass binary for Windows and package it as a
  checksummed zip archive.

.EXAMPLE
  .\packaging\build.ps1 -Target windows-x64
#>
param(
    [string]$Target = "windows-x64"
)

$ErrorActionPreference = "Stop"

Write-Host "Building CodeCompass for $Target"

Set-Location (Join-Path $PSScriptRoot "..")

python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }

pyinstaller packaging/codecompass.spec --clean --noconfirm

$exe = Join-Path "dist" "codecompass.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Expected binary dist\codecompass.exe was not produced"
    exit 1
}

# Smoke-test the binary BEFORE packaging, so a broken release never uploads.
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path "scripts" "smoke_test.ps1") -Binary $exe

$artifact = "codecompass-$Target.zip"
$artifactPath = Join-Path "dist" $artifact
if (Test-Path $artifactPath) { Remove-Item -Force $artifactPath }
Compress-Archive -Path $exe -DestinationPath $artifactPath

# Checksum (lowercase hex, "<hash>  <filename>" format like sha256sum).
$hash = (Get-FileHash $artifactPath -Algorithm SHA256).Hash.ToLower()
"$hash  $artifact" | Out-File -Encoding ascii "$artifactPath.sha256"

Write-Host "Built: $artifactPath"
Get-Content "$artifactPath.sha256"
