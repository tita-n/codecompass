#!/usr/bin/env bash
# One-line installer for CodeCompass standalone binaries.
#
#   curl -sSL https://codecompass.dev/install | sh
#
# Environment overrides:
#   CODECOMPASS_VERSION      release tag to install (default: latest)
#   CODECOMPASS_INSTALL_DIR  destination directory (default: $HOME/.local/bin)
#   CODECOMPASS_REPO         "owner/repo" (default: codecompass/codecompass)
set -euo pipefail

REPO="${CODECOMPASS_REPO:-codecompass/codecompass}"
INSTALL_DIR="${CODECOMPASS_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${CODECOMPASS_VERSION:-latest}"

# --- Detect OS/arch ---------------------------------------------------------
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$OS" in
  darwin) PLATFORM="darwin" ;;
  linux) PLATFORM="linux" ;;
  mingw*|msys*|cygwin*) PLATFORM="windows" ;;
  *) echo "error: unsupported OS: $OS" >&2; exit 1 ;;
esac

case "$ARCH" in
  arm64|aarch64) ARCH_TAG="arm64" ;;
  x86_64|amd64) ARCH_TAG="x64" ;;
  *) echo "error: unsupported arch: $ARCH" >&2; exit 1 ;;
esac

TARGET="${PLATFORM}-${ARCH_TAG}"

# --- Resolve version --------------------------------------------------------
if [ "$VERSION" = "latest" ]; then
  VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep -o '"tag_name": *"[^"]*"' | head -1 | cut -d'"' -f4)"
  if [ -z "$VERSION" ]; then
    echo "error: could not resolve latest release" >&2
    exit 1
  fi
fi

# --- Download ----------------------------------------------------------------
EXT="tar.gz"
if [ "$PLATFORM" = "windows" ]; then
  EXT="zip"
fi
URL="https://github.com/${REPO}/releases/download/${VERSION}/codecompass-${TARGET}.${EXT}"
CHECKSUM_URL="${URL}.sha256"

echo "Downloading CodeCompass ${VERSION} for ${TARGET}..."

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

curl -fsSL "$URL" -o "$TMPDIR/pkg.${EXT}"
curl -fsSL "$CHECKSUM_URL" -o "$TMPDIR/pkg.sha256"

# --- Verify checksum ----------------------------------------------------------
cd "$TMPDIR"
EXPECTED="$(awk '{print $1}' pkg.sha256)"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "pkg.${EXT}" | awk '{print $1}')"
else
  ACTUAL="$(shasum -a 256 "pkg.${EXT}" | awk '{print $1}')"
fi
if [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "error: checksum mismatch (expected $EXPECTED, got $ACTUAL)" >&2
  exit 1
fi

# --- Extract -------------------------------------------------------------------
if [ "$EXT" = "tar.gz" ]; then
  tar -xzf "pkg.${EXT}"
else
  unzip -q "pkg.${EXT}"
fi

# --- Install --------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
if [ "$PLATFORM" = "windows" ]; then
  mv -f codecompass.exe "$INSTALL_DIR/codecompass.exe"
  chmod +x "$INSTALL_DIR/codecompass.exe"
  echo "Installed to $INSTALL_DIR/codecompass.exe"
else
  mv -f codecompass "$INSTALL_DIR/codecompass"
  chmod +x "$INSTALL_DIR/codecompass"
  echo "Installed to $INSTALL_DIR/codecompass"
fi

# --- PATH hint -------------------------------------------------------------------
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    echo ""
    echo "Add to your shell profile:"
    echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
    ;;
esac

# --- Verify -----------------------------------------------------------------------
if [ "$PLATFORM" = "windows" ]; then
  "$INSTALL_DIR/codecompass.exe" --version || true
else
  "$INSTALL_DIR/codecompass" --version || true
fi
