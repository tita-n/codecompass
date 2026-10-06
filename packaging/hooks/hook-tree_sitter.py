"""PyInstaller hook for tree-sitter grammar packages.

PyInstaller's default analysis misses the compiled grammar libraries
(``*.so`` / ``*.pyd``) shipped by the ``tree_sitter_*`` language wheels,
so indexing would fail at runtime with "language not found" errors in the
frozen binary. This hook collects every native library plus the Python
bindings for all supported grammars.
"""

import contextlib

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

binaries = []
for _pkg in [
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
]:
    with contextlib.suppress(Exception):  # grammar not installed in this build env
        binaries += collect_dynamic_libs(_pkg)

hiddenimports = [
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_java",
    "tree_sitter_go",
    "tree_sitter_rust",
    "tree_sitter_php",
    "tree_sitter_cpp",
    "tree_sitter_c_sharp",
]
# Namespace-style grammar bindings (e.g. ``tree_sitter_language_pack`` style
# layouts) are picked up via submodule collection where present.
for _pkg in list(hiddenimports):
    with contextlib.suppress(Exception):
        hiddenimports += collect_submodules(_pkg)
