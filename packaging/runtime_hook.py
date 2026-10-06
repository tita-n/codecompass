"""PyInstaller runtime hook: patch library/asset paths before main runs.

Executed at startup inside the frozen binary (see ``runtime_hooks`` in
``packaging/codecompass.spec``). Normalises locations that differ between
a development checkout and the one-file bundle:

* ``TREE_SITTER_LIB_PATH`` points at the bundled grammar libraries.
* ``TOKENIZERS_PARALLELISM`` is disabled — the forked tokenizers used by
  sentence-transformers deadlock after PyInstaller's bootloader fork on
  some platforms.
* ``HF_HUB_OFFLINE`` is left untouched: the embedding model downloads on
  first use to ``~/.codecompass/models/`` (see ``docs/installation.md``).
"""

import os
import sys

if getattr(sys, "frozen", False):
    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    os.environ.setdefault("TREE_SITTER_LIB_PATH", bundle_dir)

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
