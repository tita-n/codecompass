<#
.SYNOPSIS
  Post-build smoke test for the standalone CodeCompass binary (Windows).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -Binary dist/codecompass.exe
#>
param(
    [string]$Binary = "dist/codecompass.exe"
)

$ErrorActionPreference = "Stop"

Write-Host "Smoke testing $Binary ..."

if (-not (Test-Path $Binary)) {
    Write-Error "Binary not found: $Binary"
    exit 1
}

# Isolated HOME so the developer's real ~/.codecompass is never touched.
$smokeHome = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-smoke-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $smokeHome | Out-Null
try {
    $repo = Join-Path $smokeHome "repo"
    New-Item -ItemType Directory -Path $repo | Out-Null
    @'
"""Tiny smoke-test repo."""


def hello() -> str:
    """Say hello."""
    return "world"


def main() -> None:
    """Entry point."""
    print(hello())


if __name__ == "__main__":
    main()
'@ | Out-File -Encoding utf8 (Join-Path $repo "main.py")

    $env:HOME = $smokeHome
    $env:USERPROFILE = $smokeHome

    # 1. Version + help
    (& $Binary --version) | Out-String | Select-String "CodeCompass" | Out-Null
    (& $Binary --help) | Out-String | Select-String "index" | Out-Null

    # 2. Init creates an isolated config
    & $Binary init | Out-Null
    if (-not (Test-Path (Join-Path $smokeHome ".codecompass" "config.yaml"))) {
        Write-Error "init did not create an isolated config"
        exit 1
    }

    # 3. Index the fixture repo
    (& $Binary index $repo) | Out-String | Select-String -Pattern "complete" | Out-Null

    # 4. Cross-file intelligence works from the bundle
    (& $Binary callers hello) | Out-String | Select-String "main" | Out-Null

    # 5. Daemon handshake over stdio
    $req = '{"jsonrpc":"2.0","id":1,"method":"status","params":{}}'
    $resp = $req | & $Binary daemon --repo $repo | Select-String '"id": *1' | Select-Object -First 1
    if (-not $resp -or -not ($resp.Line -match '"result"')) {
        Write-Error "daemon handshake failed"
        exit 1
    }

    Write-Host "Smoke test passed"
}
finally {
    Remove-Item -Recurse -Force $smokeHome -ErrorAction SilentlyContinue
}
