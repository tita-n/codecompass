#!/usr/bin/env bash
# Build a Debian package from an already-built Linux binary.
#
# Usage:
#   packaging/build_deb.sh [version]
#
# Requires: packaging/build.sh (or CI) to have produced dist/codecompass.
set -euo pipefail

VERSION="${1:-0.0.1}"
ARCH="amd64"
PKG="codecompass_${VERSION}_${ARCH}"

cd "$(dirname "$0")/.."

if [ ! -x "dist/codecompass" ]; then
  echo "error: dist/codecompass not found — run packaging/build.sh first" >&2
  exit 1
fi

# Keep the version in the control file in sync with the requested version.
sed -i "s/^Version: .*/Version: ${VERSION}/" packaging/deb/DEBIAN/control

mkdir -p "packaging/deb/usr/local/bin"
cp dist/codecompass "packaging/deb/usr/local/bin/"
chmod +x "packaging/deb/usr/local/bin/codecompass"

mkdir -p dist
dpkg-deb --build packaging/deb "dist/${PKG}.deb"

echo "Built: dist/${PKG}.deb"
