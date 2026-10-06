"""Typer CLI application for CodeCompass."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codecompass import __version__
from codecompass.indexer.schema import IndexingState, get_session
from codecompass.query import Analyzer, formatters
from codecompass.query.graph import CodeGraph
from codecompass.utils.config import (
    KEY_PROVIDERS,
    get_config,
    has_api_key,
    init_config,
    set_api_key,
)

app = typer.Typer(
    name="codecompass",
    help="AI-powered codebase intelligence layer",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
# Status messages that must not pollute stdout (the MCP stdio transport uses it
# for the JSON-RPC stream). Rich's Console.print has no stderr flag, so this is
# a second console bound to stderr.
error_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    """Print the version and exit (``codecompass --version``)."""
    if value:
        console.print(f"[bold green]CodeCompass[/] version [cyan]{__version__}[/]")
        raise typer.Exit()


@app.callback()
def _app_callback(
    _version: bool = typer.Option(  # noqa: FBT001
        False,
        "--version",
        "-V",
        help="Show version information and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """AI-powered codebase intelligence layer."""


config_app = typer.Typer(
    name="config",
    help="Show configuration or manage LLM credentials",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(config_app, name="config")


@app.command()
def version() -> None:
    """Show version information."""
    console.print(f"[bold green]CodeCompass[/] version [cyan]{__version__}[/]")
    console.print(f"Python: {sys.version.split()[0]}")
    console.print(f"Config path: {get_config().config_path}")


@config_app.callback(invoke_without_command=True)
def config(ctx: typer.Context) -> None:
    """Show current configuration."""
    if ctx.invoked_subcommand is not None:
        return

    cfg = get_config()
    table = Table(title="CodeCompass Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")

    for key, value in cfg.model_dump().items():
        table.add_row(key, str(value))

    console.print(table)
    console.print(
        f"[dim]LLM provider: {cfg.llm.provider} · model: {cfg.llm.model} · "
        f"stream: {cfg.llm.stream}[/]"
    )
    if cfg.llm.provider in KEY_PROVIDERS:
        state = (
            "configured (OS keyring or environment)" if has_api_key(cfg.llm.provider) else "not set"
        )
        console.print(f"[dim]API key for {cfg.llm.provider}: {state}[/]")


@config_app.command("set-key")
def set_key(
    provider: str = typer.Argument(..., help="Provider: anthropic, openai or google"),
    key: str | None = typer.Option(None, "--key", help="API key (prompted securely when omitted)"),
) -> None:
    """Store a provider API key in your OS keyring (never written to disk)."""
    normalized = provider.strip().lower()
    if normalized not in KEY_PROVIDERS:
        console.print(
            f"[red]✗[/] Cannot store keys for '{provider}'. "
            f"Supported: {', '.join(KEY_PROVIDERS)}"
        )
        raise typer.Exit(code=1)

    secret = key
    if secret is None:
        secret = typer.prompt(
            f"Enter API key for {normalized}", hide_input=True, show_default=False
        )

    try:
        set_api_key(normalized, secret)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1) from None

    console.print(f"[green]✓[/] Stored API key for {normalized} in your OS keyring")
    console.print(
        f"[dim]Set 'llm.provider: {normalized}' in {get_config().config_path} to use it.[/]"
    )


@app.command()
def init() -> None:
    """Initialize CodeCompass configuration."""
    path = init_config()
    console.print(f"[green]✓[/] Configuration created at: {path}")


@app.command()
def index(
    path: str = typer.Argument(..., help="Path to the repository to index"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-index even if already indexed"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Index a repository for CodeCompass intelligence."""
    console.print(
        Panel.fit(
            "[bold]Indexing Repository[/]",
            subtitle=f"Path: {path}",
            border_style="blue",
        )
    )

    if verbose:
        console.print("[dim]Verbose mode enabled[/]")
        console.print(f"[dim]Force: {force}[/]")

    repo_path = Path(path).absolute()
    if not repo_path.exists():
        console.print(f"[red]✗[/] Path does not exist: {path}")
        raise typer.Exit(code=1)

    if not repo_path.is_dir():
        console.print(f"[red]✗[/] Path is not a directory: {path}")
        raise typer.Exit(code=1)

    try:
        from codecompass.indexer.indexer import Indexer

        indexer = Indexer(repo_path)
        stats = indexer.index(force=force, verbose=verbose)

        console.print("\n[bold green]✓ Indexing Complete![/]")
        console.print(f"[dim]Files: {stats.get('files', 0)}[/]")
        console.print(f"[dim]Symbols: {stats.get('symbols', 0)}[/]")
        console.print(f"[dim]Calls: {stats.get('calls', 0)}[/]")
        console.print(f"[dim]Imports: {stats.get('imports', 0)}[/]")
        console.print(f"[dim]Chunks: {stats.get('chunks', 0)}[/]")

        if "duration_seconds" in stats:
            console.print(f"[dim]Duration: {stats['duration_seconds']} seconds[/]")

    except Exception as e:
        console.print(f"[red]✗[/] Error: {e}")
        raise typer.Exit(code=1) from None


def _emit_json(payload: dict[str, Any]) -> None:
    """Print a JSON document verbatim (no wrapping/markup) for piping."""
    console.print(json.dumps(payload, indent=2, default=str), markup=False, soft_wrap=True)


def _json_chunk(result: dict[str, Any]) -> dict[str, Any]:
    """JSON-serializable view of a retrieved chunk."""
    return {
        "file_path": result.get("file_path"),
        "line_start": result.get("line_start"),
        "line_end": result.get("line_end"),
        "symbol_name": result.get("symbol_name"),
        "language": result.get("language"),
        "score": round(float(result.get("score") or 0.0), 4),
        "content": result.get("document", ""),
    }


def _citations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build unique citation records from the retrieved chunks.

    Citations come from retrieval only, so the model can never introduce a file
    or line range that CodeCompass did not actually find.
    """
    seen: set[tuple[str, int, int]] = set()
    citations: list[dict[str, Any]] = []
    for result in results:
        key = (
            str(result.get("file_path")),
            int(result.get("line_start") or 0),
            int(result.get("line_end") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "file_path": key[0],
                "line_start": key[1],
                "line_end": key[2],
                "symbol_name": result.get("symbol_name"),
            }
        )
    return citations


def _print_citations(citations: list[dict[str, Any]]) -> None:
    """Render the sources table shown after an LLM answer."""
    if not citations:
        return
    table = Table(title=f"Sources ({len(citations)})")
    table.add_column("#", style="dim", justify="right")
    table.add_column("File", style="cyan")
    table.add_column("Lines", style="green")
    table.add_column("Symbol", style="magenta")
    for index, citation in enumerate(citations, 1):
        table.add_row(
            str(index),
            str(citation["file_path"]),
            f"{citation['line_start']}-{citation['line_end']}",
            str(citation.get("symbol_name") or "-"),
        )
    console.print(table)


def _chunk_preview(document: str, width: int = 150) -> str:
    preview = str(document).strip().replace("\n", " ")
    return preview[:width] + "..." if len(preview) > width else preview


def _print_chunks_table(results: list[dict[str, Any]], title: str | None = None) -> None:
    """Render retrieved chunks (the retrieval-only view from Phase 3)."""
    table = Table(title=title or f"Top {len(results)} results")
    table.add_column("File", style="cyan")
    table.add_column("Lines", style="green")
    table.add_column("Symbol", style="magenta")
    table.add_column("Score", style="yellow", justify="right")
    table.add_column("Preview", style="white")

    for result in results:
        table.add_row(
            str(result.get("file_path")),
            f"{result.get('line_start')}-{result.get('line_end')}",
            str(result.get("symbol_name") or "-"),
            f"{float(result.get('score') or 0.0):.2f}",
            _chunk_preview(result.get("document", "")),
        )
    console.print(table)


def _print_chunk_details(results: list[dict[str, Any]]) -> None:
    """Render full chunk content and score breakdown (verbose mode)."""
    for index, result in enumerate(results, 1):
        console.print(
            f"\n[bold cyan]#{index}[/] {result.get('file_path')} "
            f"({result.get('line_start')}-{result.get('line_end')})"
        )
        console.print(f"[dim]Symbol: {result.get('symbol_name') or 'N/A'}[/]")
        console.print(
            f"Score: {float(result.get('score') or 0.0):.2f} "
            f"(vector {float(result.get('vector_score') or 0.0):.2f} · "
            f"keyword {float(result.get('keyword_score') or 0.0):.2f})"
        )
        console.print(Panel(str(result.get("document", "")), title="Content", border_style="blue"))


def _print_retrieval(
    question: str, results: list[dict[str, Any]], raw: bool, verbose: bool
) -> None:
    """Emit the retrieval-only output (JSON or the chunks table)."""
    if raw:
        _emit_json(
            {
                "question": question,
                "answer": None,
                "citations": _citations(results),
                "chunks": [_json_chunk(r) for r in results],
            }
        )
        return
    _print_chunks_table(results)
    if verbose:
        _print_chunk_details(results)


def _build_orchestrator() -> Any:
    """Create the configured LLM orchestrator (raises LLMError when unusable)."""
    from codecompass.llm.factory import create_llm_client
    from codecompass.llm.orchestrator import Orchestrator

    return Orchestrator(create_llm_client(get_config().llm))


async def _stream_tokens(tokens: AsyncIterator[str]) -> None:
    """Print tokens as they arrive (token-by-token streaming).

    Markup and highlighting are disabled so code and file paths in the answer
    are never interpreted. Rich wraps per print call, and a single token is
    shorter than the terminal width, so long answers rely on the terminal's own
    line wrapping - the same behaviour as other streaming CLIs.
    """
    async for token in tokens:
        console.print(token, end="", markup=False, highlight=False)
    console.print()


async def _collect_tokens(tokens: AsyncIterator[str]) -> str:
    """Drain a token stream into a single string (used by --raw)."""
    parts: list[str] = []
    async for token in tokens:
        parts.append(token)
    return "".join(parts)


def _retrieve(question: str, limit: int, file: str | None, raw: bool) -> list[dict[str, Any]]:
    """Run hybrid retrieval, reporting empty/unindexed states.

    Returns:
        The retrieved chunks (possibly empty; the state has been reported).
    """
    from codecompass.query.search import get_latest_repo_root, hybrid_search

    if get_latest_repo_root() is None:
        if raw:
            _emit_json({"question": question, "answer": None, "error": "no index", "chunks": []})
        else:
            console.print(
                "[red]✗[/] No indexed repository found. "
                "Run [bold]codecompass index <path>[/] first."
            )
        raise typer.Exit(code=1)

    results = hybrid_search(question, limit=limit, file_path=file)
    if not results:
        if raw:
            _emit_json({"question": question, "answer": None, "chunks": []})
        else:
            console.print("[yellow]No relevant code chunks found.[/]")
    return results


def _answer_with_llm(
    question: str, results: list[dict[str, Any]], raw: bool, verbose: bool
) -> None:
    """Stream a grounded answer, then print citations.

    Any provider failure degrades to the retrieved chunks with a warning.
    """
    try:
        orchestrator = _build_orchestrator()
        if raw:
            answer = asyncio.run(_collect_tokens(orchestrator.answer_question(question, results)))
            _emit_json(
                {
                    "question": question,
                    "answer": answer,
                    "citations": _citations(results),
                    "chunks": [_json_chunk(r) for r in results],
                }
            )
            return
        console.print()
        asyncio.run(_stream_tokens(orchestrator.answer_question(question, results)))
    except Exception as exc:  # provider failure -> degrade to retrieval
        message = str(exc) or exc.__class__.__name__
        if raw:
            _emit_json(
                {
                    "question": question,
                    "answer": None,
                    "error": message,
                    "chunks": [_json_chunk(r) for r in results],
                }
            )
            return
        console.print(f"[yellow]⚠[/] LLM answer unavailable: {message}")
        console.print("[dim]Falling back to the retrieved chunks.[/]")
        _print_retrieval(question, results, raw=False, verbose=verbose)
        return

    _print_citations(_citations(results))
    if verbose:
        _print_chunk_details(results)


@app.command()
def query(
    question: str = typer.Argument(..., help="Natural language question about your codebase"),
    limit: int = typer.Option(5, "--limit", "-l", help="Number of chunks to retrieve"),
    file: str | None = typer.Option(
        None, "--file", "-f", help="Restrict results to a specific file path"
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip the LLM and print the retrieved chunks only"
    ),
    raw: bool = typer.Option(False, "--raw", help="Emit JSON (for piping)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Answer a natural-language question about your codebase.

    Retrieves the most relevant code chunks (hybrid vector + keyword search) and,
    when an LLM provider is configured, streams a grounded answer with citations.
    Falls back to the retrieved chunks when the provider is unavailable.
    """
    if not raw:
        console.print(Panel.fit(f"[bold]Query:[/] {question}", border_style="green"))

    try:
        results = _retrieve(question, limit, file, raw)
        if not results:
            return

        provider = (get_config().llm.provider or "none").strip().lower()
        if no_llm or provider == "none":
            if not raw:
                reason = "--no-llm" if no_llm else "llm.provider = 'none'"
                console.print(f"[dim]LLM skipped ({reason}). Showing retrieved chunks.[/]")
            _print_retrieval(question, results, raw=raw, verbose=verbose)
            return

        _answer_with_llm(question, results, raw=raw, verbose=verbose)
    except typer.Exit:
        raise
    except Exception as e:
        if raw:
            _emit_json({"question": question, "answer": None, "error": str(e), "chunks": []})
            return
        console.print(f"[red]Error: {e}[/]")
        if verbose:
            import traceback

            console.print(traceback.format_exc())
        raise typer.Exit(code=1) from None


def _get_query_graph() -> CodeGraph:
    """Load the CodeGraph for the most recently indexed repository."""
    from codecompass.query.graph import get_graph

    db_path = get_config().get_sqlite_path()
    session = get_session(str(db_path))
    state = session.query(IndexingState).order_by(IndexingState.last_indexed_at.desc()).first()
    if state is None:
        console.print(
            "[red]✗[/] No indexed repository found. Run [bold]codecompass index <path>[/] first."
        )
        raise typer.Exit(code=1)
    return get_graph(db_path, state.repo_root)


@app.command()
def trace(
    symbol: str = typer.Argument(..., help="Symbol name to trace"),
    depth: int = typer.Option(3, "--depth", "-d", help="Depth of dependency tree"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Trace the dependency tree (callees) of a symbol."""
    console.print(
        Panel.fit(
            f"[bold]Trace:[/] {symbol}",
            subtitle=f"Depth: {depth}",
            border_style="magenta",
        )
    )

    if verbose:
        console.print("[dim]Verbose mode enabled[/]")

    graph = _get_query_graph()
    roots = graph.get_dependency_tree(symbol, depth)
    if not roots:
        console.print(f"[yellow]⚠[/] Symbol '{symbol}' not found in the index")
        return
    console.print(formatters.dependency_tree(roots))


@app.command()
def callers(
    symbol: str = typer.Argument(..., help="Symbol name to find callers for"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Find all callers of a symbol."""
    console.print(
        Panel.fit(
            f"[bold]Callers of:[/] {symbol}",
            border_style="cyan",
        )
    )

    if verbose:
        console.print("[dim]Verbose mode enabled[/]")

    graph = _get_query_graph()
    result = graph.get_callers(symbol)
    if not result:
        console.print(f"[yellow]⚠[/] No callers found for '{symbol}'")
        return
    console.print(formatters.symbol_table(f"Callers of {symbol}", result))
    console.print(f"[dim]{len(result)} caller(s)[/]")


@app.command()
def callees(
    symbol: str = typer.Argument(..., help="Symbol name to find callees for"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Find all callees of a symbol."""
    console.print(
        Panel.fit(
            f"[bold]Callees of:[/] {symbol}",
            border_style="yellow",
        )
    )

    if verbose:
        console.print("[dim]Verbose mode enabled[/]")

    graph = _get_query_graph()
    result = graph.get_callees(symbol)
    if not result:
        console.print(f"[yellow]⚠[/] No callees found for '{symbol}'")
        return
    console.print(formatters.symbol_table(f"Callees of {symbol}", result))
    console.print(f"[dim]{len(result)} callee(s)[/]")


@app.command()
def path(
    from_symbol: str = typer.Argument(..., help="Starting symbol"),
    to_symbol: str = typer.Argument(..., help="Target symbol"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Find the call path between two symbols."""
    console.print(
        Panel.fit(
            f"[bold]Path:[/] {from_symbol} → {to_symbol}",
            border_style="red",
        )
    )

    if verbose:
        console.print("[dim]Verbose mode enabled[/]")

    graph = _get_query_graph()
    result = graph.find_path(from_symbol, to_symbol)
    if result is None:
        console.print(f"[yellow]⚠[/] No call path found from '{from_symbol}' to '{to_symbol}'")
        return
    steps = " → ".join(f"[bold cyan]{p['name']}[/]" for p in result)
    console.print(steps)
    console.print(formatters.symbol_table(f"Path from {from_symbol} to {to_symbol}", result))


@app.command()
def impact(
    symbol: str = typer.Argument(..., help="Symbol to analyze impact of"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Analyze the impact (blast radius) of changing a symbol."""
    console.print(
        Panel.fit(
            f"[bold]Impact Analysis:[/] {symbol}",
            border_style="red",
        )
    )

    if verbose:
        console.print("[dim]Verbose mode enabled[/]")

    graph = _get_query_graph()
    result = graph.get_impact(symbol)
    if result is None:
        console.print(f"[yellow]⚠[/] Symbol '{symbol}' not found in the index")
        return

    risk_color = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "green"}[result["risk"]]
    console.print(
        Panel.fit(
            f"Risk: [bold {risk_color}]{result['risk']}[/]",
            subtitle=f"{result['total_affected']} symbols transitively affected",
            border_style=risk_color,
        )
    )

    if result["direct_callers"]:
        console.print(
            formatters.symbol_table(
                f"Direct callers ({len(result['direct_callers'])}) of {symbol}",
                result["direct_callers"],
            )
        )
    else:
        console.print("[dim]No direct callers[/]")

    if result["test_files"]:
        console.print("[bold]Affected test files:[/]")
        for test_file in result["test_files"]:
            console.print(f"  [cyan]{test_file}[/]")
    else:
        console.print("[dim]No test files reference this symbol[/]")


def _language_for(path: Path) -> str:
    """Best-effort language name for a file (used for markdown fences)."""
    from codecompass.indexer.languages import get_language_for_file

    config = get_language_for_file(path)
    if config is not None:
        return config.name
    return path.suffix.lstrip(".") or "text"


def _read_code_slice(
    file_path: str, line_start: int | None, line_end: int | None
) -> tuple[str, int, int, str]:
    """Read an inclusive 1-based line range from a file.

    Returns:
        Tuple of (code, start_line, end_line, language).

    Raises:
        typer.Exit: With code 1 when the file is missing or the range invalid.
    """
    path = Path(file_path)
    if not path.is_file():
        console.print(f"[red]✗[/] File not found: {file_path}")
        raise typer.Exit(code=1)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        console.print(f"[red]✗[/] Could not read {file_path}: {exc}")
        raise typer.Exit(code=1) from None

    total = len(lines)
    start = 1 if line_start is None else line_start
    end = total if line_end is None else line_end
    if start < 1 or end < start or end > total:
        console.print(f"[red]✗[/] Invalid range {start}-{end}: {file_path} has {total} line(s)")
        raise typer.Exit(code=1)

    code = "\n".join(lines[start - 1 : end])
    return code, start, end, _language_for(path)


async def _stream_code_review(
    orchestrator: Any,
    mode: str,
    code: str,
    file_path: str,
    line_start: int,
    line_end: int,
    language: str,
) -> None:
    """Stream an explanation or simplification of a code block."""
    if mode == "simplify":
        tokens = orchestrator.simplify(code, file_path, line_start, line_end, language)
    else:
        tokens = orchestrator.explain(code, file_path, line_start, line_end, language)
    await _stream_tokens(tokens)


def _run_code_review(
    file_path: str, line_start: int | None, line_end: int | None, mode: str
) -> None:
    """Shared implementation for the ``explain`` and ``simplify`` commands."""
    code, start, end, language = _read_code_slice(file_path, line_start, line_end)
    label = "Explain" if mode == "explain" else "Simplify"
    console.print(
        Panel.fit(
            f"[bold]{label}:[/] {file_path}",
            subtitle=f"Lines: {start} → {end}",
            border_style="magenta",
        )
    )
    if mode == "simplify":
        console.print("[dim]Read-only preview — no files are modified.[/]")

    provider = (get_config().llm.provider or "none").strip().lower()
    if provider == "none":
        console.print(
            "[yellow]⚠[/] LLM is disabled (llm.provider = 'none'). "
            "Set a provider to use this command; showing the code instead:"
        )
        console.print(Panel(code, title=f"{file_path}:{start}-{end}", border_style="blue"))
        raise typer.Exit(code=1)

    try:
        orchestrator = _build_orchestrator()
        asyncio.run(
            _stream_code_review(orchestrator, mode, code, str(file_path), start, end, language)
        )
    except Exception as exc:  # provider failure -> show the code
        message = str(exc) or exc.__class__.__name__
        console.print(f"[yellow]⚠[/] LLM unavailable: {message}")
        console.print("[dim]Showing the code block instead.[/]")
        console.print(Panel(code, title=f"{file_path}:{start}-{end}", border_style="blue"))
        raise typer.Exit(code=1) from None


@app.command()
def explain(
    file_path: str = typer.Argument(..., help="Path to file"),
    line_start: int | None = typer.Option(None, "--start", "-s", help="Starting line number"),
    line_end: int | None = typer.Option(None, "--end", "-e", help="Ending line number"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Explain a section of code using the configured LLM provider."""
    if verbose:
        console.print("[dim]Verbose mode enabled[/]")
    _run_code_review(file_path, line_start, line_end, "explain")


@app.command()
def simplify(
    file_path: str = typer.Argument(..., help="Path to file"),
    line_start: int | None = typer.Option(None, "--start", "-s", help="Starting line number"),
    line_end: int | None = typer.Option(None, "--end", "-e", help="Ending line number"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Suggest a simpler version of a code section (read-only preview)."""
    if verbose:
        console.print("[dim]Verbose mode enabled[/]")
    _run_code_review(file_path, line_start, line_end, "simplify")


def _serve_mcp(transport: str, host: str, port: int) -> None:
    """Run the requested MCP transport.

    stdio keeps stdout clean (it carries the JSON-RPC stream), so status messages
    go to stderr.
    """
    from codecompass.mcp.server import run_http, run_stdio

    if transport == "stdio":
        error_console.print("[dim]Ready - connect an agent via stdio.[/]")
        asyncio.run(run_stdio())
    elif transport == "http":
        console.print(f"[dim]Listening on http://{host}:{port} (streamable HTTP)[/]")
        asyncio.run(run_http(host=host, port=port))
    else:
        console.print(f"[red]✗[/] Unknown transport: {transport} (use stdio or http)")
        raise typer.Exit(code=1)


@app.command()
def mcp(
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio or http"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host for HTTP transport"),
    port: int = typer.Option(8888, "--port", "-p", help="Port for HTTP transport"),
    list_tools: bool = typer.Option(
        False, "--list-tools", help="Print the exposed tool names and exit"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Start the MCP server so AI agents can query your codebase."""
    if list_tools:
        from codecompass.mcp.handlers import HANDLERS

        table = Table(title="CodeCompass MCP tools")
        table.add_column("Tool", style="cyan")
        for name in HANDLERS:
            table.add_row(name)
        console.print(table)
        return

    if verbose:
        error_console.print("[dim]Verbose mode enabled[/]")

    # stdio owns stdout for the JSON-RPC stream: keep chatter off it.
    on_stderr = transport == "stdio"
    status_console = error_console if on_stderr else console
    status_console.print(
        Panel.fit(
            "[bold]CodeCompass MCP Server[/]",
            subtitle=f"Transport: {transport}",
            border_style="blue",
        )
    )

    if host not in ("127.0.0.1", "localhost", "::1"):
        status_console.print(f"[yellow]⚠[/] Binding to {host} exposes your index to the network.")

    try:
        _serve_mcp(transport, host, port)
    except KeyboardInterrupt:
        error_console.print("\n[yellow]MCP server stopped.[/]")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]✗[/] {e}")
        if verbose:
            import traceback

            error_console.print(traceback.format_exc())
        raise typer.Exit(code=1) from None


@app.command()
def daemon(
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository root (default: cwd if it is a git checkout, else the most recently indexed repository)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log request timings to stderr"),
) -> None:
    """Run CodeCompass as a JSON-RPC daemon over stdio (used by the VS Code extension).

    Reads newline-delimited JSON-RPC requests on stdin and writes responses on
    stdout; stdout carries nothing else, so it can be piped directly.
    """
    from codecompass.api.daemon import run_daemon

    if verbose:
        error_console.print("[dim]Daemon verbose mode enabled[/]")
    error_console.print("[dim]CodeCompass daemon ready (JSON-RPC over stdio).[/]")
    try:
        code = run_daemon(repo_root=repo, verbose=verbose)
    except KeyboardInterrupt:
        error_console.print("\n[yellow]Daemon stopped.[/]")
        code = 0
    raise typer.Exit(code=code)


@app.command()
def analyze(
    mode: str = typer.Argument(..., help="Analysis mode: hotspots, coupling, circular"),
    threshold: int | None = typer.Option(
        None, "--threshold", "-t", help="Threshold for hotspots/coupling"
    ),
    limit: int = typer.Option(50, "--limit", "-l", help="Max rows to display"),
    all_cycles: bool = typer.Option(
        False, "--all", help="For circular: enumerate every cycle (slow on dense repos)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Analyze complexity hotspots, coupling, and circular dependencies."""
    if not json_output:
        console.print(
            Panel.fit(
                f"[bold]Analyze:[/] {mode}",
                subtitle=f"Threshold: {threshold or 'default'}",
                border_style="magenta",
            )
        )

    if verbose and not json_output:
        console.print("[dim]Verbose mode enabled[/]")

    graph = _get_query_graph()
    analyzer = Analyzer(graph)

    if mode == "hotspots":
        effective_threshold = threshold if threshold is not None else 10
        rows = analyzer.hotspots(effective_threshold, limit)
        if json_output:
            _emit_json({"mode": "hotspots", "threshold": threshold or 10, "hotspots": rows})
            return
        if not rows:
            console.print(f"[green]✓[/] No functions exceed complexity {effective_threshold}")
            return
        if json_output:
            _emit_json({"mode": "hotspots", "threshold": effective_threshold, "hotspots": rows})
            return
        console.print(formatters.hotspots_table(rows))
    elif mode == "coupling":
        effective_threshold = threshold if threshold is not None else 5
        rows = analyzer.coupling(effective_threshold)
        if json_output:
            _emit_json({"mode": "coupling", "threshold": threshold or 5, "modules": rows})
            return
        if not rows:
            console.print(f"[green]✓[/] No modules exceed {effective_threshold} imports")
            return
        if json_output:
            _emit_json({"mode": "coupling", "threshold": effective_threshold, "modules": rows})
            return
        console.print(formatters.coupling_table(rows))
    elif mode == "circular":
        cycles = analyzer.cycles(find_all=all_cycles)
        if json_output:
            _emit_json({"mode": "circular", "count": len(cycles), "cycles": cycles})
            return
        if not cycles:
            console.print("[green]✓[/] No circular dependencies detected")
            return
        if json_output:
            _emit_json({"mode": "circular", "count": len(cycles), "cycles": cycles})
            return
        console.print(formatters.circular_tree(cycles))
    else:
        console.print(f"[red]✗[/] Unknown mode: '{mode}' (use hotspots, coupling, or circular)")
        raise typer.Exit(code=1)


def main() -> None:
    """Entry point for the CLI."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/] Interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
