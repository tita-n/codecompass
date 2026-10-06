"""MCP server exposing CodeCompass intelligence to AI agents.

Nine read-only tools over the local index (see :mod:`codecompass.mcp.handlers`).
The server never calls an LLM, the network or a cloud API - it answers purely
from the local SQLite/ChromaDB data built by ``codecompass index``.

Tool calls are logged to ``~/.codecompass/logs/mcp.log`` (rotating, 10 MB x 3).
Only symbol names, file paths and timings are logged - never file contents.
"""

import json
import logging
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from typing import Any

from mcp.server.mcpserver import MCPServer

from codecompass.api import handlers
from codecompass.utils.config import get_config

SERVER_NAME = "codecompass"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3

_INSTRUCTIONS = """\
CodeCompass exposes precomputed knowledge about an indexed repository: symbol
locations, cross-file callers/callees, import relationships, dependency graphs
and impact analysis. Prefer these tools over grep/read: they are exact, fast
(milliseconds) and cannot hallucinate file paths. Re-run `codecompass index
<path>` after code changes."""


def configure_logging() -> logging.Logger:
    """Configure and return the rotating MCP call logger."""
    logger = logging.getLogger("codecompass.mcp")
    if logger.handlers:
        return logger

    log_dir = get_config().base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "mcp.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _loggable_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Bound argument values for the log.

    Tool arguments are identifiers (symbol/module names, depths) - never file
    contents - so logging them is safe and makes the log genuinely useful for
    debugging. Long strings are truncated to keep lines readable.
    """
    return {
        key: f"{value[:80]}..." if isinstance(value, str) and len(value) > 80 else value
        for key, value in arguments.items()
    }


def _dispatch(
    logger: logging.Logger, tool: str, arguments: dict[str, Any], func: Callable[[], dict[str, Any]]
) -> str:
    """Run a handler, log the call and return JSON (or an error message)."""
    started = time.perf_counter()
    try:
        payload = func()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.warning(
            "tool=%s args=%s error=%s: %s duration_ms=%.1f",
            tool,
            _loggable_args(arguments),
            type(exc).__name__,
            exc,
            elapsed_ms,
        )
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "tool": tool}, indent=2)

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info("tool=%s args=%s duration_ms=%.1f", tool, _loggable_args(arguments), elapsed_ms)
    return json.dumps(payload, indent=2, default=str)


def create_server(repo_root: str | None = None) -> MCPServer:
    """Create the MCP server with all nine CodeCompass tools registered."""
    logger = configure_logging()
    server = MCPServer(SERVER_NAME, instructions=_INSTRUCTIONS, version="1")

    @server.tool(
        name="find_symbol",
        description=(
            "Find a symbol (function, class, method) by name. Returns file path, "
            "line range, signature, docstring and cyclomatic complexity. Use this "
            "first instead of grepping."
        ),
    )
    def find_symbol(name: str, kind: str | None = None) -> str:
        """Locate symbols by name, optionally filtered by kind."""
        return _dispatch(
            logger,
            "find_symbol",
            {"name": name, "kind": kind},
            lambda: handlers.find_symbol(name, kind, repo_root),
        )

    @server.tool(
        name="find_callers",
        description=(
            "Find every symbol that calls the given symbol, across files and "
            "including tests. Returns file, line range and the call site line."
        ),
    )
    def find_callers(symbol: str) -> str:
        """Find cross-file callers of a symbol."""
        return _dispatch(
            logger,
            "find_callers",
            {"symbol": symbol},
            lambda: handlers.find_callers(symbol, repo_root),
        )

    @server.tool(
        name="find_callees",
        description=(
            "Find every symbol called by the given symbol. Returns file, line "
            "range and signature for each callee."
        ),
    )
    def find_callees(symbol: str) -> str:
        """Find the symbols a given symbol calls."""
        return _dispatch(
            logger,
            "find_callees",
            {"symbol": symbol},
            lambda: handlers.find_callees(symbol, repo_root),
        )

    @server.tool(
        name="find_imports",
        description=(
            "Show who imports a module (e.g. 'app.payments') and what that module "
            "itself imports. Returns file, line and imported names."
        ),
    )
    def find_imports(module: str) -> str:
        """Show importers and imports for a module."""
        return _dispatch(
            logger,
            "find_imports",
            {"module": module},
            lambda: handlers.find_imports(module, repo_root),
        )

    @server.tool(
        name="get_dependency_graph",
        description=(
            "Return the callee graph around a symbol as flat node/edge lists "
            "(caller -> callee). Cycle-safe; depth defaults to 2."
        ),
    )
    def get_dependency_graph(symbol: str, depth: int = 2) -> str:
        """Return a dependency subgraph around a symbol."""
        return _dispatch(
            logger,
            "get_dependency_graph",
            {"symbol": symbol, "depth": depth},
            lambda: handlers.get_dependency_graph(symbol, depth, repo_root),
        )

    @server.tool(
        name="impact_analysis",
        description=(
            "Blast radius of changing a symbol: risk (HIGH/MEDIUM/LOW), direct and "
            "total caller counts, affected files and the tests that cover it."
        ),
    )
    def impact_analysis(symbol: str, max_depth: int = 3) -> str:
        """Assess the impact of changing a symbol."""
        return _dispatch(
            logger,
            "impact_analysis",
            {"symbol": symbol, "max_depth": max_depth},
            lambda: handlers.impact_analysis(symbol, max_depth, repo_root),
        )

    @server.tool(
        name="analyze_hotspots",
        description=(
            "Find the most complex functions/methods by cyclomatic complexity. "
            "Returns name, kind, file, line range, complexity and size, ranked "
            "worst first. Use this when asked what to refactor first."
        ),
    )
    def analyze_hotspots(threshold: int = 10, limit: int = 50) -> str:
        """List complexity hotspots at or above the threshold."""
        return _dispatch(
            logger,
            "analyze_hotspots",
            {"threshold": threshold, "limit": limit},
            lambda: handlers.analyze_hotspots(threshold, limit, repo_root),
        )

    @server.tool(
        name="analyze_coupling",
        description=(
            "Find tightly coupled modules by import fan-in/fan-out. Returns "
            "file, fan-in, fan-out, instability (0=stable, 1=unstable) and "
            "total coupling, ranked worst first."
        ),
    )
    def analyze_coupling(threshold: int = 5) -> str:
        """List modules with (fan-in + fan-out) at or above the threshold."""
        return _dispatch(
            logger,
            "analyze_coupling",
            {"threshold": threshold},
            lambda: handlers.analyze_coupling(threshold, repo_root),
        )

    @server.tool(
        name="analyze_circular",
        description=(
            "Detect circular dependencies between modules. Returns the list "
            "of import cycles (each a list of file paths) plus the first "
            "cycle as edge pairs."
        ),
    )
    def analyze_circular(max_cycles: int = 100, find_all: bool = False) -> str:
        """Detect import cycles (bounded by default, exhaustive with find_all)."""
        return _dispatch(
            logger,
            "analyze_circular",
            {"max_cycles": max_cycles, "find_all": find_all},
            lambda: handlers.analyze_circular(max_cycles, find_all, repo_root),
        )

    return server


async def run_stdio(repo_root: str | None = None) -> None:
    """Serve MCP over stdio (the transport used by Claude Code and Cursor)."""
    configure_logging().info("starting MCP server transport=stdio")
    await create_server(repo_root).run_stdio_async()


async def run_http(host: str = "127.0.0.1", port: int = 8888, repo_root: str | None = None) -> None:
    """Serve MCP over streamable HTTP, bound to loopback by default."""
    logger = configure_logging()
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("MCP HTTP transport bound to %s (not loopback)", host)
    logger.info("starting MCP server transport=streamable-http host=%s port=%d", host, port)
    await create_server(repo_root).run_streamable_http_async(host=host, port=port)


def describe_tools() -> list[dict[str, str]]:
    """Return the registered tool names (used by diagnostics and tests)."""
    return [{"name": name} for name in handlers.HANDLERS]
