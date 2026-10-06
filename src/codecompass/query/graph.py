"""In-memory NetworkX call graph built from the indexed database."""

import re
from pathlib import Path

import networkx as nx

from codecompass.indexer.schema import Call, File, Import, Session, Symbol
from codecompass.query.resolver import SymbolResolver

# Relative paths that look like test files.
_TEST_FILE_PATTERNS = (
    re.compile(r"(^|/)test_.*\.py$"),
    re.compile(r"(^|/).*_test\.py$"),
    re.compile(r"(^|/)(tests|test)/"),
)

_CALLER_LIMIT = 200


class CodeGraph:
    """A queryable view of the codebase call graph."""

    def __init__(
        self,
        session: Session,
        repo_root: str | Path,
        resolver: SymbolResolver | None = None,
    ) -> None:
        self.session = session
        self.repo_root = Path(repo_root)
        self.resolver = resolver or SymbolResolver(session, str(self.repo_root))

        self.graph = nx.DiGraph()  # symbol ids -> symbol ids (CALLS edges)
        self.module_graph = nx.DiGraph()  # file relpath -> file relpath (IMPORTS edges)
        self.symbols: list[dict] = []
        self.symbol_index: dict[str, list[dict]] = {}
        self.file_import_counts: dict[str, int] = {}

        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        # Persist any cross-file resolutions before building the graph.
        self.resolver.resolve_unresolved_calls()

        rows = (
            self.session.query(
                Symbol.id,
                Symbol.name,
                Symbol.kind,
                Symbol.signature,
                Symbol.line_start,
                Symbol.complexity,
                File.relative_path,
            )
            .join(File)
            .filter(File.repo_root == str(self.repo_root))
            .all()
        )
        for symbol_id, name, kind, signature, line_start, complexity, relative_path in rows:
            node = {
                "id": symbol_id,
                "name": name,
                "kind": kind,
                "signature": signature,
                "line": line_start,
                "complexity": complexity,
                "file": relative_path,
            }
            self.graph.add_node(symbol_id, **node)
            self.symbols.append(node)
            self.symbol_index.setdefault(name, []).append(node)

        call_rows = (
            self.session.query(Call.caller_id, Call.callee_id, Call.line, Call.call_text)
            .join(Symbol, Call.caller_id == Symbol.id)
            .join(File, Symbol.file_id == File.id)
            .filter(File.repo_root == str(self.repo_root), Call.callee_id.isnot(None))
            .all()
        )
        for caller_id, callee_id, line, call_text in call_rows:
            if caller_id in self.graph and callee_id in self.graph:
                self.graph.add_edge(caller_id, callee_id, line=line, call_text=call_text)

        self._load_module_graph()

    def _load_module_graph(self) -> None:
        files = (
            self.session.query(File.id, File.relative_path)
            .filter_by(repo_root=str(self.repo_root))
            .all()
        )
        file_id_to_path: dict[int, str] = {}
        for file_id, relative_path in files:
            file_id_to_path[file_id] = relative_path

        import_rows = (
            self.session.query(
                Import.file_id, Import.module, Import.imported_name, Import.is_from_import
            )
            .join(File)
            .filter(File.repo_root == str(self.repo_root))
            .all()
        )
        for file_id, module, imported_name, is_from_import in import_rows:
            source = file_id_to_path.get(file_id)
            if source is None:
                continue
            self.file_import_counts[source] = self.file_import_counts.get(source, 0) + 1
            target = self.resolver.resolve_import_target_file(
                source, module, imported_name, is_from_import
            )
            if target is not None and target != source:
                self.module_graph.add_edge(source, target)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def _nodes_named(self, name: str) -> list[dict]:
        """Return all symbol dicts with the given name."""
        return list(self.symbol_index.get(name, []))

    def get_callers(self, symbol_name: str) -> list[dict]:
        """Return all symbols that call any symbol named ``symbol_name``."""
        results: list[dict] = []
        seen: set[tuple[int, int]] = set()
        for node in self._nodes_named(symbol_name):
            for caller_id in self.graph.predecessors(node["id"]):
                edge = self.graph.get_edge_data(caller_id, node["id"]) or {}
                key = (caller_id, edge.get("line") or 0)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "name": self.graph.nodes[caller_id]["name"],
                        "kind": self.graph.nodes[caller_id]["kind"],
                        "file": self.graph.nodes[caller_id]["file"],
                        "line": edge.get("line"),
                        "signature": self.graph.nodes[caller_id]["signature"],
                        "call_text": edge.get("call_text"),
                    }
                )
        return sorted(results, key=lambda r: (str(r["file"]), r["line"] or 0))[:_CALLER_LIMIT]

    def get_callees(self, symbol_name: str) -> list[dict]:
        """Return all symbols called by any symbol named ``symbol_name``."""
        results: list[dict] = []
        seen: set[int] = set()
        for node in self._nodes_named(symbol_name):
            for callee_id in self.graph.successors(node["id"]):
                if callee_id in seen:
                    continue
                seen.add(callee_id)
                results.append(
                    {
                        "name": self.graph.nodes[callee_id]["name"],
                        "kind": self.graph.nodes[callee_id]["kind"],
                        "file": self.graph.nodes[callee_id]["file"],
                        "line": self.graph.nodes[callee_id]["line"],
                        "signature": self.graph.nodes[callee_id]["signature"],
                    }
                )
        return sorted(results, key=lambda r: (str(r["file"]), r["line"] or 0))

    def get_dependency_tree(self, symbol_name: str, depth: int = 3) -> list[dict]:
        """Return the callee tree of ``symbol_name`` up to ``depth`` levels."""
        return [self._build_tree(node, depth) for node in self._nodes_named(symbol_name)]

    def _build_tree(self, node: dict, depth: int) -> dict:
        root = {
            "name": node["name"],
            "kind": node["kind"],
            "file": node["file"],
            "line": node["line"],
            "children": [],
        }
        if depth > 0:
            self._expand_tree(root, node["id"], depth - 1, set())
        return root

    def _expand_tree(self, entry: dict, symbol_id: int, remaining: int, seen: set) -> None:
        for callee_id in self.graph.successors(symbol_id):
            key = (callee_id, remaining)
            if key in seen:
                continue
            seen.add(key)
            node = self.graph.nodes[callee_id]
            child = {
                "name": node["name"],
                "kind": node["kind"],
                "file": node["file"],
                "line": node["line"],
                "children": [],
            }
            entry["children"].append(child)
            if remaining > 0:
                self._expand_tree(child, callee_id, remaining - 1, seen)

    def find_path(self, from_symbol: str, to_symbol: str) -> list[dict] | None:
        """Return the shortest call path between two symbols, or ``None``."""
        starts = [node["id"] for node in self._nodes_named(from_symbol)]
        ends = [node["id"] for node in self._nodes_named(to_symbol)]
        if not starts or not ends:
            return None

        best: list[int] | None = None
        for start in starts:
            for end in ends:
                if start == end:
                    path: list[int] = [start]
                else:
                    try:
                        path = nx.shortest_path(self.graph, start, end)
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue
                if best is None or len(path) < len(best):
                    best = path
        if best is None:
            return None
        return [dict(self.graph.nodes[symbol_id]) for symbol_id in best]

    def get_impact(self, symbol_name: str, depth: int = 3) -> dict | None:
        """Compute the blast radius of a symbol: callers, tests, and risk score."""
        nodes = self._nodes_named(symbol_name)
        if not nodes:
            return None

        direct_ids: list[int] = []
        ancestor_ids: list[int] = []
        for node in nodes:
            ancestors = self._ancestors(node["id"], depth)
            direct_ids.extend(
                [a for a in ancestors if a in set(self.graph.predecessors(node["id"]))]
            )
            ancestor_ids.extend(ancestors)

        direct_ids = sorted(set(direct_ids))
        ancestor_ids = sorted(set(ancestor_ids))
        direct_callers = [
            {
                "name": self.graph.nodes[sid]["name"],
                "kind": self.graph.nodes[sid]["kind"],
                "file": self.graph.nodes[sid]["file"],
                "line": self.graph.nodes[sid]["line"],
                "signature": self.graph.nodes[sid]["signature"],
            }
            for sid in direct_ids
        ]

        count = len(direct_ids)
        if count > 5:
            risk = "HIGH"
        elif count >= 2:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        return {
            "symbol": symbol_name,
            "risk": risk,
            "direct_callers": direct_callers,
            "total_affected": len(ancestor_ids),
            "test_files": self.find_test_files(symbol_name),
        }

    def _ancestors(self, symbol_id: int, depth: int) -> list[int]:
        """Collect all transitive callers of a symbol up to ``depth`` levels."""
        found: dict[int, int] = {}
        frontier: list[int] = [symbol_id]
        for level in range(1, depth + 1):
            next_frontier: list[int] = []
            for current in frontier:
                for predecessor in self.graph.predecessors(current):
                    if predecessor not in found:
                        found[predecessor] = level
                        next_frontier.append(predecessor)
            frontier = next_frontier
        return list(found)

    def find_test_files(self, symbol_name: str) -> list[str]:
        """Return test files in the repo that reference ``symbol_name``."""
        files = {node["file"] for node in self.symbols}
        files.update(self.file_import_counts)
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        matches: list[str] = []
        for relative_path in sorted(files):
            if not any(p.search(relative_path) for p in _TEST_FILE_PATTERNS):
                continue
            file_path = self.repo_root / relative_path
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pattern.search(content):
                matches.append(relative_path)
        return matches


# ---------------------------------------------------------------------------
# Graph cache
# ---------------------------------------------------------------------------

_graph_cache: dict[tuple, CodeGraph] = {}


def _fingerprint(session: Session, repo_root: str) -> tuple:
    """A cheap signature of the indexed data for cache invalidation."""
    return (
        session.query(Symbol.id).join(File).filter(File.repo_root == repo_root).count(),
        session.query(Call.id)
        .join(Symbol, Call.caller_id == Symbol.id)
        .join(File, Symbol.file_id == File.id)
        .filter(File.repo_root == repo_root)
        .count(),
    )


def get_graph(
    db_path: str | Path,
    repo_root: str | Path,
    force: bool = False,
) -> CodeGraph:
    """Load (or return the cached) ``CodeGraph`` for a repository."""
    from codecompass.indexer.schema import get_session

    session = get_session(str(db_path))
    root = str(repo_root)
    key = (str(db_path), root, _fingerprint(session, root))
    if not force and key in _graph_cache:
        return _graph_cache[key]
    graph = CodeGraph(session, root)
    _graph_cache.clear()
    _graph_cache[key] = graph
    return graph
