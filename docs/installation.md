# Installation

CodeCompass ships as a **standalone binary** — no Python, pip, or build tools required.

## One-line install (macOS / Linux)

```bash
curl -sSL https://codecompass.dev/install | sh
```

The script detects your OS/arch, downloads the right release asset from
GitHub Releases, verifies its SHA256 checksum, and installs to
`~/.local/bin` (override with `CODECOMPASS_INSTALL_DIR`).

Pin a version:

```bash
CODECOMPASS_VERSION=v0.0.1 curl -sSL https://codecompass.dev/install | sh
```

The installer serves [`packaging/install.sh`](../packaging/install.sh).

## Homebrew (macOS)

```bash
brew install codecompass/tap/codecompass
```

The formula lives in [`packaging/homebrew/codecompass.rb`](../packaging/homebrew/codecompass.rb)
(published to the `codecompass/homebrew-tap` tap). After a GitHub Release,
update its `version` and the two `sha256` values from the release's
`.sha256` files.

> Unsigned binary? If macOS Gatekeeper blocks the first launch, run:
> `xattr -d com.apple.quarantine ~/.local/bin/codecompass`
> (not needed for the Homebrew install).

## Windows (Winget)

```powershell
winget install CodeCompass.CodeCompass
```

The manifest lives in [`packaging/winget/codecompass.yaml`](../packaging/winget/codecompass.yaml)
— bump `PackageVersion`, `InstallerUrl`, and `InstallerSha256` per release
and submit via PR to `microsoft/winget-pkgs`. Validate locally with
`winget validate packaging/winget/codecompass.yaml`.

Portable alternative: download `codecompass-windows-x64.zip` from
GitHub Releases, verify it against the attached `.sha256`, and put
`codecompass.exe` on your `PATH`.

## Debian / Ubuntu

Download the `.deb` from GitHub Releases and install:

```bash
sudo dpkg -i codecompass_0.0.1_amd64.deb
```

The package installs the binary to `/usr/local/bin/codecompass`
(see [`packaging/deb/DEBIAN/control`](../packaging/deb/DEBIAN/control)).
Maintainers build it with `packaging/build.sh linux-x64` followed by
`packaging/build_deb.sh 0.0.1`.

## Manual (any platform)

1. Pick the asset for your platform from GitHub Releases:
   `codecompass-darwin-arm64.tar.gz`, `codecompass-darwin-x64.tar.gz`,
   `codecompass-linux-x64.tar.gz` (+ `.deb`), or `codecompass-windows-x64.zip`.
2. Download the matching `.sha256` file and verify it:
   `sha256sum -c codecompass-<target>.tar.gz.sha256`.
3. Extract and put `codecompass` (or `codecompass.exe`) on your `PATH`.

## Verify your install

```bash
codecompass --version
codecompass init
codecompass index /path/to/repo
codecompass query "where is main defined?"
```

## How it works

The binaries are built with PyInstaller from
[`packaging/codecompass.spec`](../packaging/codecompass.spec) — one file
per target, built by [`.github/workflows/release.yml`](../.github/workflows/release.yml)
on every `v*` tag. A custom hook
([`packaging/hooks/hook-tree_sitter.py`](../packaging/hooks/hook-tree_sitter.py))
bundles the tree-sitter grammar libraries that PyInstaller otherwise misses,
and a runtime hook
([`packaging/runtime_hook.py`](../packaging/runtime_hook.py)) fixes up
library paths at startup.

Notes:

- The ~90 MB embedding model is **not** bundled — it downloads on first use
  to `~/.codecompass/models/` (the standard sentence-transformers cache).
- Every build runs [`scripts/smoke_test.sh`](../scripts/smoke_test.sh)
  (`smoke_test.ps1` on Windows) against the fresh binary **before**
  packaging, so a broken release never uploads.

## From source (contributors)

```bash
pip install codecompass        # or: pip install -e . from a checkout
```

See the Development section in the README.
