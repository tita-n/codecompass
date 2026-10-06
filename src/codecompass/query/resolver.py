"""Cross-file symbol resolution for the CodeCompass query engine.

The indexer stores call sites with a ``callee_name`` and a ``callee_id`` that is
only set when the callee lives in the same file. This module builds an
in-memory registry of every exported symbol (keyed by dotted module path) plus
a per-file import map, and uses it to resolve the remaining call sites across
files — handling aliases, relative imports, wildcards and module-qualified
calls.
"""

from pathlib import Path

from codecompass.indexer.schema import Call, File, Import, Session, Symbol


def module_path_from_file(relative_path: str) -> str:
    """Convert a file's relative path to a dotted module path.

    ``app/calculator.py`` -> ``app.calculator``
    ``app/__init__.py`` -> ``app``
    ``__init__.py`` (repo root) -> ``""``
    """
    path = Path(relative_path)
    parts = path.parts[:-1] if path.name == "__init__.py" else path.with_suffix("").parts
    return ".".join(parts)


class SymbolResolver:
    """Resolves call sites to symbol definitions using imports."""

    def __init__(self, session: Session, repo_root: str | Path) -> None:
        self.session = session
        self.repo_root = str(repo_root)

        # (module path, exported name) -> symbol ids
        self.symbol_map: dict[tuple[str, str], list[int]] = {}
        # (file relpath, local name) -> symbol ids imported into that file
        self.import_map: dict[tuple[str, str], list[int]] = {}
        # (file relpath, local name) -> dotted module path (plain imports)
        self.module_alias_map: dict[tuple[str, str], str] = {}
        # (file relpath, name) -> symbol ids defined in that file
        self.symbol_by_file_name: dict[tuple[str, str], list[int]] = {}
        # (parent symbol id, child name) -> symbol ids (methods, nested funcs)
        self.child_map: dict[tuple[int, str], list[int]] = {}
        # file relpath -> dotted module path
        self.file_module: dict[str, str] = {}
        # dotted module path -> file relpath (first file claiming that module)
        self.module_files: dict[str, str] = {}
        # symbol id -> file relpath
        self.symbol_file: dict[int, str] = {}

        self._build_maps()

    # ------------------------------------------------------------------
    # Map construction
    # ------------------------------------------------------------------

    def _build_maps(self) -> None:
        files = (
            self.session.query(File.id, File.relative_path)
            .filter_by(repo_root=self.repo_root)
            .all()
        )
        file_id_to_path: dict[int, str] = {}
        for file_id, relative_path in files:
            file_id_to_path[file_id] = relative_path
            self.file_module[relative_path] = module_path_from_file(relative_path)
            self.module_files.setdefault(self.file_module[relative_path], relative_path)

        symbols = (
            self.session.query(
                Symbol.id, Symbol.name, Symbol.kind, Symbol.parent_symbol_id, Symbol.file_id
            )
            .join(File)
            .filter(File.repo_root == self.repo_root)
            .all()
        )
        for symbol_id, name, _kind, parent_symbol_id, file_id in symbols:
            relative_path = file_id_to_path.get(file_id)
            if relative_path is None:
                continue
            module = self.file_module[relative_path]
            self.symbol_map.setdefault((module, name), []).append(symbol_id)
            self.symbol_by_file_name.setdefault((relative_path, name), []).append(symbol_id)
            if parent_symbol_id is not None:
                self.child_map.setdefault((parent_symbol_id, name), []).append(symbol_id)
            self.symbol_file[symbol_id] = relative_path

        imports = (
            self.session.query(
                Import.module,
                Import.imported_name,
                Import.alias,
                Import.is_from_import,
                Import.file_id,
            )
            .join(File)
            .filter(File.repo_root == self.repo_root)
            .all()
        )
        for module, imported_name, alias, is_from_import, file_id in imports:
            relative_path = file_id_to_path.get(file_id)
            if relative_path is None or not module:
                continue
            if is_from_import:
                self._map_from_import(relative_path, module, imported_name, alias)
            else:
                self._map_plain_import(relative_path, module, alias)

    def _map_from_import(
        self, file_path: str, module: str, imported_name: str | None, alias: str | None
    ) -> None:
        """Record a ``from <module> import <name> [as <alias>]`` statement."""
        target_module = self._resolve_module_path(self.file_module.get(file_path, ""), module)
        if target_module is None:
            return
        local_name = alias or imported_name
        if not local_name:
            return

        if imported_name == "*":
            # Wildcard: bind every exported name of the target module.
            for (module_name, exported), ids in self.symbol_map.items():
                if module_name == target_module:
                    for symbol_id in ids:
                        self.import_map.setdefault((file_path, exported), []).append(symbol_id)
            return

        candidates = self.symbol_map.get((target_module, imported_name or ""), [])
        if candidates:
            self.import_map.setdefault((file_path, local_name), []).extend(candidates)
        else:
            # ``from app import calculator`` where calculator is a submodule
            submodule = f"{target_module}.{imported_name}" if target_module else imported_name
            if submodule in self.module_files:
                self.module_alias_map[(file_path, local_name)] = submodule

    def _map_plain_import(self, file_path: str, module: str, alias: str | None) -> None:
        """Record a plain ``import <module> [as <alias>]`` statement."""
        # ``import a.b`` binds the top-level name ``a`` unless aliased.
        local_name = alias or module.split(".")[0]
        self.module_alias_map[(file_path, local_name)] = module

    def _resolve_module_path(self, importing_module: str, module_text: str) -> str | None:
        """Resolve a (possibly relative) import module text to a dotted path."""
        if not module_text.startswith("."):
            return module_text or None
        dots = len(module_text) - len(module_text.lstrip("."))
        suffix = module_text.lstrip(".")
        base = importing_module.split(".") if importing_module else []
        base = base[: max(0, len(base) - dots)]
        parts = base + ([suffix] if suffix else [])
        return ".".join(parts) or None

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve_call(self, caller_file: str, callee_name: str | None) -> int | None:
        """Resolve a call site to a single symbol id, or ``None`` if ambiguous/external."""
        if not callee_name:
            return None
        parts = callee_name.split(".")
        leaf = parts[-1]

        if len(parts) == 1:
            return self._unique(self.import_map.get((caller_file, leaf), [])) or self._unique(
                self.symbol_by_file_name.get((caller_file, leaf), [])
            )

        root = parts[0]
        if root in ("self", "cls"):
            return self._unique(self.symbol_by_file_name.get((caller_file, leaf), []))

        # Root is a from-imported symbol (e.g. a class) -> resolve its child method.
        for candidate in self.import_map.get((caller_file, root), []):
            child = self._unique(self.child_map.get((candidate, parts[1]), []))
            if child is not None:
                return child

        # Root is a module alias (e.g. ``import app.auth as auth``) -> resolve in module.
        module = self.module_alias_map.get((caller_file, root))
        if module:
            return self._unique(self.symbol_map.get((module, parts[1]), []))

        return None

    def resolve_unresolved_calls(self) -> int:
        """Resolve calls with no callee yet, persisting cross-file edges.

        Returns the number of calls newly resolved.
        """
        calls = (
            self.session.query(Call)
            .join(Symbol, Call.caller_id == Symbol.id)
            .join(File, Symbol.file_id == File.id)
            .filter(File.repo_root == self.repo_root, Call.callee_id.is_(None))
            .all()
        )
        resolved = 0
        for call in calls:
            caller_file = self.symbol_file.get(call.caller_id)
            if caller_file is None:
                continue
            target = self.resolve_call(caller_file, call.callee_name)
            if target is not None:
                call.callee_id = target
                resolved += 1
        if resolved:
            self.session.commit()
        return resolved

    def resolve_import_target_file(
        self,
        file_path: str,
        module: str,
        imported_name: str | None,
        is_from_import: int,
    ) -> str | None:
        """Resolve an import to the target file within the repo, if any."""
        if is_from_import:
            target_module = self._resolve_module_path(self.file_module.get(file_path, ""), module)
            if target_module is None:
                return None
            if imported_name in (None, "*"):
                return self.module_files.get(target_module)
            submodule = f"{target_module}.{imported_name}" if target_module else imported_name
            if submodule in self.module_files:
                return self.module_files[submodule]
            symbol_id = self._unique(self.symbol_map.get((target_module, imported_name), []))
            return self.symbol_file.get(symbol_id) if symbol_id is not None else None
        return self.module_files.get(module)

    @staticmethod
    def _unique(ids: list[int]) -> int | None:
        """Return the only id in a list, or ``None`` when ambiguous."""
        return ids[0] if len(ids) == 1 else None
