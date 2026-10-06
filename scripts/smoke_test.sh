#!/usr/bin/env bash
# Post-build smoke test for the standalone CodeCompass binary.
#
# Usage:
#   scripts/smoke_test.sh [binary]
#
# Exercises the same surface the VS Code extension and install docs rely on:
# version output, config init, indexing a tiny repo, a cross-file "callers"
# lookup, and a daemon JSON-RPC handshake. Uses an isolated HOME so the
# developer's real ~/.codecompass is never touched.
#
# Packaged binaries must not require Python on PATH: the test re-runs the
# core assertions with a PATH that hides every python interpreter.
set -euo pipefail

BINARY="${1:-dist/codecompass}"

echo "Smoke testing $BINARY ..."

if [ ! -x "$BINARY" ]; then
  echo "error: binary not found or not executable: $BINARY" >&2
  exit 1
fi

# --- Isolated HOME -------------------------------------------------------------
SMOKE_HOME="$(mktemp -d)"
trap 'rm -rf "$SMOKE_HOME"' EXIT

# Fake repo: main() calls hello() so `callers hello` must mention main.
mkdir -p "$SMOKE_HOME/repo"
cat > "$SMOKE_HOME/repo/main.py" <<'EOF'
"""Tiny smoke-test repo."""


def hello() -> str:
    """Say hello."""
    return "world"


def main() -> None:
    """Entry point."""
    print(hello())


if __name__ == "__main__":
    main()
EOF

run() {
  HOME="$SMOKE_HOME" "$BINARY" "$@"
}

# 1. Version + help -------------------------------------------------------------
run --version | grep -q "CodeCompass"
run --help | grep -q "index"

# 2. Init creates an isolated config --------------------------------------------
run init
test -f "$SMOKE_HOME/.codecompass/config.yaml"

# 3. Index the fixture repo --------------------------------------------------------
run index "$SMOKE_HOME/repo" | grep -qi "complete"

# 4. Cross-file intelligence works from the bundle ----------------------------------
run callers hello | grep -q "main"

# 5. Daemon handshake over stdio ------------------------------------------------------
DAEMON_REQ='{"jsonrpc":"2.0","id":1,"method":"status","params":{}}'
DAEMON_RESP="$(echo "$DAEMON_REQ" | HOME="$SMOKE_HOME" "$BINARY" daemon --repo "$SMOKE_HOME/repo" | grep -m1 '"id":1')"
echo "$DAEMON_RESP" | grep -q '"result"'

# 6. No Python required ---------------------------------------------------------------
# Re-run version + a lookup with every python hidden from PATH.
mkdir -p "$SMOKE_HOME/emptybin"
for tool in sh grep awk cat head; do
  # shellcheck disable=SC2230
  src="$(command -v "$tool")"
  ln -sf "$src" "$SMOKE_HOME/emptybin/$tool"
done
if PATH="$SMOKE_HOME/emptybin" "$BINARY" --version | grep -q "CodeCompass" \
  && PATH="$SMOKE_HOME/emptybin" HOME="$SMOKE_HOME" "$BINARY" callers hello | grep -q "main"; then
  echo "(no-python check passed: binary runs without a python interpreter on PATH)"
else
  echo "error: binary appears to require Python on PATH" >&2
  exit 1
fi

echo "Smoke test passed"
