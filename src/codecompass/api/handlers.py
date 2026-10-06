"""Shared intelligence handlers used by every CodeCompass surface.

``codecompass.mcp.server`` (MCP tools for AI agents) and
``codecompass.api.daemon`` (JSON-RPC over stdio for the VS Code extension)
both dispatch here, so every consumer sees exactly the same answers.

Every handler returns a plain JSON-serialisable ``dict`` with a stable schema
(clients can rely on the shape and on ``count`` fields to short-circuit) or
raises :class:`ToolError` / :class:`InvalidParams` for misuse - the transports
decide how to render those errors. The intelligence comes from the Phase 1-4
query engine (``CodeGraph``, hybrid search, ``Orchestrator``); this module
only shapes its output and fills in file/line metadata.

Besides the six MCP tools, this module hosts the daemon-facing handlers
(``index``, ``status``, ``analyze_*``) and the three streaming helpers
(``stream_query`` / ``stream_code_review``) that talk to the configured LLM -
and only when the caller actually invokes them.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codecompass import __version__
from codecompass.indexer.schema import Chunk, File, Import, IndexingState, Symbol, get_session
from codecompass.query.analyzer import Analyzer
from codecompass.query.graph import CodeGraph, get_graph
from codecompass.query.resolver import module_path_from_file
from codecompass.query.search import get_latest_repo_root, hybrid_search
from codecompass.utils.config import get_config

if TYPE_CHECKING:
    from codecompass.llm.orchestrator import Orchestrator

#: Upper bound for depth arguments so a confused client cannot ask for the world.
MAX_DEPTH = 10

#: Upper bound for retrieval ``limit`` in ``stream_query``.
MAX_QUERY_LIMIT = 20

#: Signature of the incremental sink used by the streaming helpers.
TokenSink = Callable[[str], None]


class ToolError(Exception):
    """Raised for tool misuse or a missing index (reported back to the client)."""


class InvalidParams(ToolError):
    """Raised when a request's parameters are missing or malformed.

    Transports map this to JSON-RPC error code ``-32602``; plain ``ToolError``
    maps to ``-32603``.
    """


def load_graph(repo_root: str | None = None) -> CodeGraph:
    """Load the code graph for ``repo_root`` (most recently indexed by default).

    Raises:
        ToolError: If no repository has been indexed, or ``repo_root`` names a
            repository that was never indexed.
    """
    config = get_config()
    db_path = str(config.get_sqlite_path())

    session = get_session(db_path)
    try:
        if repo_root is None:
            state = (
                session.query(IndexingState).order_by(IndexingState.last_indexed_at.desc()).first()
            )
            if state is None:
                raise ToolError(
                    "No indexed repository found. Run 'codecompass index <path>' first."
                )
            repo_root = state.repo_root
        else:
            state = (
                session.query(IndexingState)
                .filter(IndexingState.repo_root == str(repo_root))
                .first()
            )
            if state is None:
                raise ToolError(
                    f"Repository {repo_root} is not indexed. "
                    f"Run 'codecompass index {repo_root}' first."
                )
    finally:
        session.close()

    try:
        return get_graph(db_path, repo_root)
    except Exception as exc:  # missing/corrupt database
        raise ToolError(f"Could not open the index for {repo_root}: {exc}") from exc


def _clamp_depth(depth: int | None, default: int) -> int:
    if depth is None:
        return default
    return max(1, min(int(depth), MAX_DEPTH))


def _symbol_details(graph: CodeGraph, names: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    """Return ``{(file, name): symbol details}`` for the given symbol names."""
    if not names:
        return {}
    rows = (
        graph.session.query(
            Symbol.name,
            Symbol.kind,
            Symbol.line_start,
            Symbol.line_end,
            Symbol.signature,
            Symbol.docstring,
            Symbol.complexity,
            File.relative_path,
        )
        .join(File, Symbol.file_id == File.id)
        .filter(File.repo_root == str(graph.repo_root), Symbol.name.in_(names))
        .all()
    )
    details: dict[tuple[str, str], dict[str, Any]] = {}
    for name, kind, line_start, line_end, signature, docstring, complexity, path in rows:
        details[(path, name)] = {
            "name": name,
            "kind": kind,
            "file": path,
            "line_start": line_start,
            "line_end": line_end,
            "signature": signature,
            "docstring": docstring,
            "complexity": complexity,
        }
    return details


# ---------------------------------------------------------------------------
# MCP tools (also reachable through the daemon as find_symbol, ..., impact)
# ---------------------------------------------------------------------------


def find_symbol(name: str, kind: str | None = None, repo_root: str | None = None) -> dict[str, Any]:
    """Tool 1: locate symbols by name (optionally filtered by kind)."""
    if not name or not name.strip():
        raise ToolError("find_symbol requires a non-empty 'name'.")

    graph = load_graph(repo_root)
    nodes = graph.symbol_index.get(name, [])
    if kind:
        nodes = [node for node in nodes if node["kind"] == kind]

    details = _symbol_details(graph, {name})
    symbols = [
        details.get((node["file"], name))
        or {
            "name": name,
            "kind": node["kind"],
            "file": node["file"],
            "line_start": node["line"],
            "line_end": node["line"],
            "signature": node["signature"],
            "docstring": None,
            "complexity": node["complexity"],
        }
        for node in nodes
    ]
    return {"found": bool(symbols), "name": name, "count": len(symbols), "symbols": symbols}


def _related(
    graph: CodeGraph, symbol: str, rows: list[dict[str, Any]], relation: str
) -> dict[str, Any]:
    """Shared shape for find_callers / find_callees."""
    details = _symbol_details(graph, {row["name"] for row in rows})
    entries: list[dict[str, Any]] = []
    for row in rows:
        info = details.get((row["file"], row["name"]), {})
        entry: dict[str, Any] = {
            "name": row["name"],
            "kind": row.get("kind") or info.get("kind"),
            "file": row["file"],
            "line_start": info.get("line_start", row.get("line")),
            "line_end": info.get("line_end", row.get("line")),
            "signature": row.get("signature") or info.get("signature"),
        }
        if "call_line" in row or row.get("line") is not None:
            entry["call_line"] = row.get("line")
        entries.append(entry)

    return {"symbol": symbol, relation: entries, "count": len(entries)}


def find_callers(symbol: str, repo_root: str | None = None) -> dict[str, Any]:
    """Tool 2: every symbol that calls ``symbol`` (across files, incl. tests)."""
    if not symbol or not symbol.strip():
        raise ToolError("find_callers requires a non-empty 'symbol'.")
    graph = load_graph(repo_root)
    return _related(graph, symbol, graph.get_callers(symbol), "callers")


def find_callees(symbol: str, repo_root: str | None = None) -> dict[str, Any]:
    """Tool 3: every symbol called by ``symbol``.

    ``CodeGraph.get_callees`` returns each callee's *definition* line, so the
    call-site lines are read from the graph edges here to keep ``call_line``
    meaningful in the response.
    """
    if not symbol or not symbol.strip():
        raise ToolError("find_callees requires a non-empty 'symbol'.")

    graph = load_graph(repo_root)
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in graph.symbol_index.get(symbol, []):
        for callee_id in graph.graph.successors(node["id"]):
            if int(callee_id) in seen:
                continue
            seen.add(int(callee_id))
            callee = graph.graph.nodes[callee_id]
            edge = graph.graph.get_edge_data(node["id"], callee_id) or {}
            rows.append(
                {
                    "name": callee["name"],
                    "kind": callee["kind"],
                    "file": callee["file"],
                    "line": edge.get("line"),  # call site
                    "signature": callee["signature"],
                }
            )
    rows.sort(key=lambda row: (str(row["file"]), row["line"] or 0))
    return _related(graph, symbol, rows, "callees")


def find_imports(module: str, repo_root: str | None = None) -> dict[str, Any]:
    """Tool 4: who imports ``module``, and what ``module`` itself imports."""
    if not module or not module.strip():
        raise ToolError("find_imports requires a non-empty 'module'.")

    graph = load_graph(repo_root)
    session = graph.session
    repo = str(graph.repo_root)
    candidates = {module, module.replace(".", "/"), module.replace("/", ".")}

    file_paths = [
        str(row[0])
        for row in session.query(File.relative_path).filter(File.repo_root == repo).all()
    ]
    module_files: dict[str, str] = {}
    for path in sorted(file_paths):
        module_files.setdefault(module_path_from_file(path), path)
    # Accept a file path as well as a dotted module name.
    target_file = module_files.get(module) or (module if module in file_paths else None)

    rows = (
        session.query(
            File.relative_path,
            Import.module,
            Import.imported_name,
            Import.line,
            Import.is_from_import,
        )
        .join(Import, Import.file_id == File.id)
        .filter(File.repo_root == repo)
        .all()
    )

    imported_by: dict[str, dict[str, Any]] = {}
    imports: dict[str, dict[str, Any]] = {}

    for path, imported_module, imported_name, line, is_from in rows:
        module_key = str(imported_module or "")
        # Who imports the requested module?
        if module_key in candidates or module_key.endswith(f".{module}"):
            entry = imported_by.setdefault(path, {"file": path, "line": line, "names": []})
            if imported_name and imported_name not in entry["names"]:
                entry["names"].append(imported_name)
        # What does the requested module import itself?
        if target_file is not None and path == target_file:
            entry = imports.setdefault(
                module_key,
                {"module": module_key, "names": [], "line": line, "is_from": bool(is_from)},
            )
            if imported_name and imported_name not in entry["names"]:
                entry["names"].append(imported_name)

    imported_by_list = sorted(imported_by.values(), key=lambda e: str(e["file"]))
    imports_list = sorted(imports.values(), key=lambda e: str(e["module"]))
    return {
        "module": module,
        "file": target_file,
        "imported_by": imported_by_list,
        "imported_by_count": len(imported_by_list),
        "imports": imports_list,
        "imports_count": len(imports_list),
    }


def get_dependency_graph(
    symbol: str, depth: int = 2, repo_root: str | None = None
) -> dict[str, Any]:
    """Tool 5: callee graph around ``symbol`` as flat node/edge lists.

    Breadth-first with a visited set, so cyclic calls terminate instead of
    recursing forever.
    """
    if not symbol or not symbol.strip():
        raise ToolError("get_dependency_graph requires a non-empty 'symbol'.")

    graph = load_graph(repo_root)
    limit = _clamp_depth(depth, 2)

    roots = graph.symbol_index.get(symbol, [])
    details = _symbol_details(graph, {symbol})
    visited: set[int] = set()
    order: list[int] = []
    edges: list[dict[str, Any]] = []
    frontier = [int(node["id"]) for node in roots]

    for _level in range(limit + 1):
        next_frontier: list[int] = []
        for symbol_id in frontier:
            if symbol_id not in visited:
                visited.add(symbol_id)
                order.append(symbol_id)
            for callee_id in graph.graph.successors(symbol_id):
                edge = graph.graph.get_edge_data(symbol_id, callee_id) or {}
                edges.append(
                    {
                        "from_id": symbol_id,
                        "to_id": callee_id,
                        "line": edge.get("line"),
                        "call_text": edge.get("call_text"),
                    }
                )
                if int(callee_id) not in visited:
                    next_frontier.append(int(callee_id))
        frontier = [sid for sid in next_frontier if sid not in visited]
        if not frontier:
            break

    # Assign agent-friendly ids: the bare symbol name when it is unambiguous in
    # this traversal, otherwise ``name@file:line``.
    name_counts: dict[str, int] = {}
    for symbol_id in order:
        name = str(graph.graph.nodes[symbol_id]["name"])
        name_counts[name] = name_counts.get(name, 0) + 1

    ids: dict[int, str] = {}
    nodes: list[dict[str, Any]] = []
    for symbol_id in order:
        node = graph.graph.nodes[symbol_id]
        name = str(node["name"])
        ids[symbol_id] = f"{name}@{node['file']}:{node['line']}" if name_counts[name] > 1 else name
        info = details.get((str(node["file"]), name), {})
        nodes.append(
            {
                "id": ids[symbol_id],
                "name": name,
                "kind": node["kind"],
                "file": node["file"],
                "line_start": info.get("line_start", node["line"]),
                "line_end": info.get("line_end", node["line"]),
                "signature": node["signature"],
            }
        )

    return {
        "root": symbol,
        "found": bool(roots),
        "depth": limit,
        "nodes": nodes,
        "edges": [
            {
                "from": ids.get(int(edge["from_id"]), ""),
                "to": ids.get(int(edge["to_id"]), ""),
                "line": edge["line"],
            }
            for edge in edges
        ],
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def _transitive_callers(graph: CodeGraph, symbol: str, limit: int) -> set[int]:
    """Return the symbol itself plus every transitive caller (cycle-safe)."""
    roots = [int(node["id"]) for node in graph.symbol_index.get(symbol, [])]
    affected: set[int] = set(roots)
    frontier = list(roots)
    for _level in range(limit):
        next_frontier: list[int] = []
        for current in frontier:
            for predecessor in graph.graph.predecessors(current):
                pid = int(predecessor)
                if pid not in affected:
                    affected.add(pid)
                    next_frontier.append(pid)
        frontier = next_frontier
        if not frontier:
            break
    return affected


def impact_analysis(
    symbol: str, max_depth: int = 3, repo_root: str | None = None
) -> dict[str, Any]:
    """Tool 6: blast radius of changing ``symbol`` (risk, files, tests).

    Risk comes from :meth:`CodeGraph.get_impact` (the same heuristic the CLI
    uses) so agents and humans always agree on HIGH/MEDIUM/LOW.
    """
    if not symbol or not symbol.strip():
        raise ToolError("impact_analysis requires a non-empty 'symbol'.")

    graph = load_graph(repo_root)
    limit = _clamp_depth(max_depth, 3)

    impact = graph.get_impact(symbol, depth=limit)
    if impact is None:
        return {
            "symbol": symbol,
            "found": False,
            "risk": "UNKNOWN",
            "direct_callers": 0,
            "total_affected": 0,
            "affected_files": [],
            "affected_tests": [],
            "reasoning": f"Symbol '{symbol}' is not present in the index.",
        }

    affected_ids = _transitive_callers(graph, symbol, limit)
    affected_files = sorted(
        {str(graph.graph.nodes[symbol_id]["file"]) for symbol_id in affected_ids}
    )
    direct_callers = len(impact["direct_callers"])
    test_files = list(impact["test_files"])
    reasoning = (
        f"{direct_callers} direct caller(s), {impact['total_affected']} total affected "
        f"symbol(s) across {len(affected_files)} file(s) (max_depth={limit}). "
        f"{len(test_files)} test file(s) exercise this symbol."
    )

    return {
        "symbol": symbol,
        "found": True,
        "risk": impact["risk"],
        "direct_callers": direct_callers,
        "total_affected": impact["total_affected"],
        "affected_files": affected_files,
        "affected_tests": test_files,
        "reasoning": reasoning,
    }


# The MCP server and `codecompass mcp --list-tools` enumerate these so new
# tools don't need a second registration site — but `mcp.handlers` (the
# historical import path) re-exports this mapping, so keep it importable.
#: Tool name -> handler (used by the MCP dispatcher, the daemon and by tests).
#: NOTE: populated after the analyze_* handlers are defined below (they must
#: exist before the dict literal evaluates).
HANDLERS: dict = {}


# ---------------------------------------------------------------------------
# Analysis / maintenance handlers (daemon + CLI-facing)
# ---------------------------------------------------------------------------


def analyze_hotspots(
    threshold: int = 10, limit: int = 50, repo_root: str | None = None
) -> dict[str, Any]:
    """Complexity hotspots: functions/methods at or above the cyclomatic threshold."""
    graph = load_graph(repo_root)
    rows = Analyzer(graph).hotspots(int(threshold), int(limit))
    hotspots = [
        {
            "name": row.get("name"),
            "kind": row.get("kind"),
            "file": row.get("file"),
            "line_start": row.get("line_start", row.get("line")),
            "line_end": row.get("line_end", row.get("line")),
            "line": row.get("line"),
            "complexity": row.get("complexity") or 0,
            "size": row.get("size") or 0,
        }
        for row in rows
    ]
    return {
        "threshold": int(threshold),
        "limit": int(limit),
        "count": len(hotspots),
        "hotspots": hotspots,
    }


def analyze_coupling(threshold: int = 5, repo_root: str | None = None) -> dict[str, Any]:
    """Coupling hotspots: files with (fan-in + fan-out) at or above ``threshold``."""
    graph = load_graph(repo_root)
    rows = Analyzer(graph).coupling(int(threshold))
    modules = [
        {
            "file": row.get("file"),
            "path": row.get("file"),
            "imports": row.get("imports") or 0,
            "imported_by": row.get("imported_by") or 0,
            "fan_in": row.get("fan_in") or 0,
            "fan_out": row.get("fan_out") or 0,
            "instability": row.get("instability") or 0.0,
            "total": row.get("total") or 0,
            "coupling": row.get("coupling", row.get("total")) or 0,
        }
        for row in rows
    ]
    return {"threshold": int(threshold), "count": len(modules), "modules": modules}


def analyze_circular(
    max_cycles: int = 100, find_all: bool = False, repo_root: str | None = None
) -> dict[str, Any]:
    """Import cycles in the module graph (bounded by default, ``find_all`` for more)."""
    graph = load_graph(repo_root)
    analyzer = Analyzer(graph)
    cycles = analyzer.cycles(max_cycles=int(max_cycles), find_all=bool(find_all))
    first = analyzer.circular()
    edges = [{"from": source, "to": target} for source, target in (first or [])]
    return {"circular": bool(cycles), "count": len(cycles), "cycles": cycles, "cycle": edges}


def index_repo(repo_path: str, force: bool = False) -> dict[str, Any]:
    """(Re)index ``repo_path`` and return the same statistics as the CLI.

    The indexer's stdout is captured: the daemon owns stdout for the JSON-RPC
    stream, so stray ``print`` output must never interleave with responses
    (tqdm already writes to stderr on its own).
    """
    import contextlib
    import io

    from codecompass.indexer.indexer import Indexer  # heavy: tree-sitter, watchdog

    root = Path(repo_path).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"Not a directory: {root}")

    with contextlib.redirect_stdout(io.StringIO()):
        stats = Indexer(root).index(force=force, verbose=False)
    return {"repo_root": str(root), "force": bool(force), "ok": True, **stats}


def status(repo_root: str | None = None) -> dict[str, Any]:
    """Index state, per-repo counts and LLM settings (for status bars)."""
    config = get_config()
    session = get_session(str(config.get_sqlite_path()))
    try:
        state_query = session.query(IndexingState)
        if repo_root is None:
            state = state_query.order_by(IndexingState.last_indexed_at.desc()).first()
        else:
            state = state_query.filter(IndexingState.repo_root == str(repo_root)).first()

        repo = state.repo_root if state is not None else repo_root
        files = symbols = chunks = 0
        if repo is not None:
            files = session.query(File).filter(File.repo_root == str(repo)).count()
            symbols = (
                session.query(Symbol)
                .join(File, Symbol.file_id == File.id)
                .filter(File.repo_root == str(repo))
                .count()
            )
            chunks = (
                session.query(Chunk)
                .join(File, Chunk.file_id == File.id)
                .filter(File.repo_root == str(repo))
                .count()
            )
    finally:
        session.close()

    return {
        "indexed": state is not None,
        "repo_root": repo,
        "state": state.status if state is not None else None,
        "last_indexed_at": state.last_indexed_at.isoformat() if state is not None else None,
        "files": files,
        "symbols": symbols,
        "chunks": chunks,
        "llm_provider": config.llm.provider or "none",
        "llm_model": config.llm.model,
        "version": __version__,
    }


# ---------------------------------------------------------------------------
# Streaming helpers (LLM-backed; used by the daemon's query/explain/simplify)
# ---------------------------------------------------------------------------


def build_orchestrator() -> "Orchestrator":
    """Create the configured LLM orchestrator (raises when unusable)."""
    from codecompass.llm.factory import create_llm_client
    from codecompass.llm.orchestrator import Orchestrator

    return Orchestrator(create_llm_client(get_config().llm))


def _build_orchestrator() -> "Orchestrator":
    """Like :func:`build_orchestrator` but reports failures as ``ToolError``."""
    try:
        return build_orchestrator()
    except Exception as exc:  # LLMError: unknown provider / missing key
        raise ToolError(str(exc) or type(exc).__name__) from exc


def _require_llm() -> None:
    """Fail fast with an actionable message when the LLM layer is disabled."""
    provider = (get_config().llm.provider or "none").strip().lower()
    if provider == "none":
        raise ToolError(
            "LLM is disabled (llm.provider = 'none'). Set a provider in "
            "~/.codecompass/config.yaml or in the CodeCompass VS Code settings."
        )


def _source_entry(chunk: dict[str, Any]) -> dict[str, Any]:
    """Trim a retrieved chunk down to the citation-table fields."""
    return {
        "file": chunk.get("file_path"),
        "line_start": chunk.get("line_start"),
        "line_end": chunk.get("line_end"),
        "symbol_name": chunk.get("symbol_name"),
        "score": round(float(chunk.get("score") or 0.0), 3),
    }


async def stream_query(
    question: str,
    limit: int = 5,
    repo_root: str | None = None,
    on_token: TokenSink | None = None,
) -> dict[str, Any]:
    """Retrieve context for ``question`` and stream a grounded answer.

    ``on_token`` (when given) receives the answer incrementally; the returned
    dict is the final ``done`` payload: ``question``, ``count``, ``sources``.
    """
    if not question or not question.strip():
        raise InvalidParams("'question' must be a non-empty string")
    limit = max(1, min(int(limit), MAX_QUERY_LIMIT))

    if repo_root is None and get_latest_repo_root() is None:
        raise ToolError("No indexed repository found. Run 'codecompass index <path>' first.")

    chunks = hybrid_search(question, limit=limit, repo_root=repo_root)
    sources = [_source_entry(chunk) for chunk in chunks]

    if not chunks:
        # Retrieval is the source of truth: report an empty result instead of
        # letting the LLM guess without context.
        if on_token:
            on_token(
                "No relevant context found in the index. "
                "Run 'CodeCompass: Index Workspace' and try again."
            )
        return {"question": question, "count": 0, "sources": []}

    _require_llm()
    orchestrator = _build_orchestrator()
    async for token in orchestrator.answer_question(question, chunks):
        if on_token:
            on_token(token)
    return {"question": question, "count": len(sources), "sources": sources}


def resolve_file(file_path: str, repo_root: str | None = None) -> Path:
    """Resolve a (possibly repo-relative) file path to an absolute one."""
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return path
    root = repo_root or get_latest_repo_root()
    if root is None:
        raise ToolError("No indexed repository found. Run 'codecompass index <path>' first.")
    return Path(root) / path


def language_for(path: Path) -> str:
    """Best-effort language name for a file (used for markdown fences)."""
    from codecompass.indexer.languages import get_language_for_file

    config = get_language_for_file(path)
    if config is not None:
        return config.name
    return path.suffix.lstrip(".") or "text"


def read_code_slice(
    path: Path, line_start: int | None = None, line_end: int | None = None
) -> tuple[str, int, int, str]:
    """Read an inclusive 1-based line range from ``path``.

    Returns:
        ``(code, start_line, end_line, language)``.

    Raises:
        ToolError: If the file is missing or unreadable.
        InvalidParams: If the requested range is invalid.
    """
    if not path.is_file():
        raise ToolError(f"File not found: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(f"Could not read {path}: {exc}") from exc

    total = len(lines)
    start = 1 if line_start is None else int(line_start)
    end = total if line_end is None else int(line_end)
    if start < 1 or end < start or end > total:
        raise InvalidParams(f"Invalid range {start}-{end}: {path} has {total} line(s)")

    code = "\n".join(lines[start - 1 : end])
    return code, start, end, language_for(path)


async def stream_code_review(
    mode: str,
    file_path: str,
    line_start: int | None = None,
    line_end: int | None = None,
    repo_root: str | None = None,
    on_token: TokenSink | None = None,
) -> dict[str, Any]:
    """Stream an explanation (``mode='explain'``) or a simplification preview.

    Read-only by contract: ``simplify`` suggests changes but never writes.
    """
    if mode not in ("explain", "simplify"):
        raise InvalidParams(f"'mode' must be 'explain' or 'simplify', got {mode!r}.")

    path = resolve_file(file_path, repo_root)
    code, start, end, language = read_code_slice(path, line_start, line_end)

    _require_llm()
    orchestrator = _build_orchestrator()
    if mode == "simplify":
        tokens = orchestrator.simplify(code, str(path), start, end, language)
    else:
        tokens = orchestrator.explain(code, str(path), start, end, language)
    async for token in tokens:
        if on_token:
            on_token(token)
    return {"file": str(path), "line_start": start, "line_end": end, "mode": mode}


# Populate the tool registry now that every handler above is defined.
HANDLERS.update(
    {
        "find_symbol": find_symbol,
        "find_callers": find_callers,
        "find_callees": find_callees,
        "find_imports": find_imports,
        "get_dependency_graph": get_dependency_graph,
        "impact_analysis": impact_analysis,
        "analyze_hotspots": analyze_hotspots,
        "analyze_coupling": analyze_coupling,
        "analyze_circular": analyze_circular,
    }
)
