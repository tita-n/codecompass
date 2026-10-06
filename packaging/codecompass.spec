# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the CodeCompass standalone binary.

Builds a one-file ``codecompass`` executable with no Python required at
runtime. Heavy dependencies (tree-sitter grammars, torch, chromadb,
transformers) are collected explicitly because PyInstaller's default hooks
miss their native libraries and data files.

Usage::

    pyinstaller packaging/codecompass.spec --clean --noconfirm

The embedding model weights (~90 MB) are intentionally NOT bundled: they
download on first use to ``~/.codecompass/models/`` via the normal
sentence-transformers cache. This keeps the binary smaller.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPEC_DIR = Path(SPECPATH)  # noqa: F821  (injected by PyInstaller)
PROJECT_ROOT = SPEC_DIR.parent

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

# Heavy packages whose data files / native libs / submodules PyInstaller
# cannot discover on its own. Each entry is best-effort: packages that are
# not installed are skipped so the spec still works for partial checkouts.
for pkg in [
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_java",
    "tree_sitter_go",
    "tree_sitter_rust",
    "tree_sitter_php",
    "tree_sitter_cpp",
    "tree_sitter_c_sharp",
    "chromadb",
    "sentence_transformers",
    "transformers",
    "torch",
    "tokenizers",
    "huggingface_hub",
    "networkx",
    "sqlalchemy",
    "anthropic",
    "openai",
    "google.generativeai",
    "mcp",
    "typer",
    "rich",
    "pydantic",
    "keyring",
    "watchdog",
    "tqdm",
]:
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # package not installed in this environment
        continue
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("codecompass")

block_cipher = None

a = Analysis(  # noqa: F821 (injected by PyInstaller)
    [str(PROJECT_ROOT / "src" / "codecompass" / "cli" / "main.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(SPEC_DIR / "hooks")],
    runtime_hooks=[str(SPEC_DIR / "runtime_hook.py")],
    excludes=[
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "wx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe_kwargs: dict = {
    "debug": False,
    "bootloader_ignore_signals": False,
    "strip": False,
    "upx": True,
    "runtime_tmpdir": None,
    "console": True,
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}

icon_ico = SPEC_DIR / "icon.ico"
icon_icns = SPEC_DIR / "icon.icns"
if sys.platform == "win32" and icon_ico.exists():
    exe_kwargs["icon"] = str(icon_ico)
elif sys.platform == "darwin" and icon_icns.exists():
    exe_kwargs["icon"] = str(icon_icns)

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="codecompass",
    **exe_kwargs,
)

# macOS: additionally bundle as CodeCompass.app for Finder launches.
if sys.platform == "darwin":
    BUNDLE(  # noqa: F821
        exe,
        name="CodeCompass.app",
        icon=str(icon_icns) if icon_icns.exists() else None,
        bundle_identifier="dev.codecompass.cli",
    )
