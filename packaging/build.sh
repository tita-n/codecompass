#!/usr/bin/env bash
# Build the standalone CodeCompass binary for macOS/Linux and package it as
# a checksummed tarball.
#
# Usage:
#   packaging/build.sh [target]
#
# The target defaults to the current machine, e.g. "linux-x64" or
# "darwin-arm64". CI passes it explicitly (see .github/workflows/release.yml).
set -euo pipefail

TARGET="${1:-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)}"

# Normalise uname spellings to the release-asset spellings.
case "$TARGET" in
  linux-x86_64|linux-amd64) TARGET="linux-x64" ;;
  linux-aarch64) TARGET="linux-arm64" ;;
  darwin-x86_64|darwin-amd64) TARGET="darwin-x64" ;;
  darwin-arm64|darwin-aarch64) TARGET="darwin-arm64" ;;
esac

echo "Building CodeCompass for $TARGET"

cd "$(dirname "$0")/.."

# Ensure packaging + project dependencies are present.
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .

# Clean previous build output (keep everything else).
rm -rf build dist

# Run PyInstaller (spec lives in packaging/, output lands in dist/).
pyinstaller packaging/codecompass.spec --clean --noconfirm

if [ ! -x "dist/codecompass" ]; then
  echo "error: expected binary dist/codecompass was not produced" >&2
  exit 1
fi

# Smoke-test the binary BEFORE packaging, so a broken release never uploads.
bash scripts/smoke_test.sh dist/codecompass

# Package artifact.
ARTIFACT="codecompass-${TARGET}.tar.gz"
tar -czf "dist/${ARTIFACT}" -C dist codecompass

# Checksum (BSD shasum on macOS, GNU sha256sum on Linux — prefer either).
if command -v sha256sum >/dev/null 2>&1; then
  (cd dist && sha256sum "${ARTIFACT}" > "${ARTIFACT}.sha256")
else
  (cd dist && shasum -a 256 "${ARTIFACT}" > "${ARTIFACT}.sha256")
fi

echo "Built: dist/${ARTIFACT}"
cat "dist/${ARTIFACT}.sha256"
