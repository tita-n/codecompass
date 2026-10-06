"""JSON-RPC-over-stdio daemon backing the CodeCompass VS Code extension.

Newline-delimited JSON on stdin/stdout: one object per line, no framing bytes.
stdout carries *only* protocol messages - all diagnostics go to stderr. One
request is processed at a time, and streaming methods emit incrementally as
tokens arrive::

    -> {"id": 1, "method": "find_symbol", "params": {"name": "login"}}
    <- {"id": 1, "result": {"found": true, ...}}

    -> {"id": 2, "method": "query", "params": {"question": "Where is auth?"}}
    <- {"id": 2, "stream": "The "}
    <- {"id": 2, "stream": "auth logic ..."}
    <- {"id": 2, "done": true, "count": 3, "sources": [...]}

    -> {"id": 3, "method": "shutdown"}
    <- {"id": 3, "result": {"ok": true}}

Error codes follow JSON-RPC 2.0: ``-32700`` parse, ``-32600`` invalid request,
``-32601`` unknown method, ``-32602`` invalid params, ``-32603`` everything
else. All intelligence lives in :mod:`codecompass.api.handlers`; this module
only does transport concerns (dispatch, framing, error mapping).
"""

import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from codecompass import __version__
from codecompass.api import handlers
from codecompass.api.handlers import MAX_DEPTH, InvalidParams, ToolError
from codecompass.query.search import get_latest_repo_root

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: Sends one protocol message (the run loop writes it as a JSON line).
Responder = Callable[[dict[str, Any]], None]
#: Synchronous handlers: ``(params, repo_root) -> JSON-serialisable result``.
SyncHandler = Callable[[dict[str, Any], str | None], dict[str, Any]]


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error object."""
    return {"id": request_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Parameter validation (maps malformed params to -32602)
# ---------------------------------------------------------------------------


def require_str(params: dict[str, Any], key: str) -> str:
    """Return ``params[key]`` or raise :class:`InvalidParams`."""
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidParams(f"'{key}' must be a non-empty string")
    return value


def optional_str(params: dict[str, Any], key: str) -> str | None:
    """Return ``params[key]`` as a stripped string, or ``None`` when absent."""
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidParams(f"'{key}' must be a string")
    stripped = value.strip()
    return stripped or None


def optional_int(
    params: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = 100,
) -> int:
    """Return ``params[key]`` clamped to ``[minimum, maximum]``, or ``default``."""
    value = params.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParams(f"'{key}' must be an integer")
    return max(minimum, min(value, maximum))


def optional_bool(params: dict[str, Any], key: str, default: bool = False) -> bool:
    """Return ``params[key]`` as a bool, or ``default`` when absent."""
    value = params.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise InvalidParams(f"'{key}' must be a boolean")
    return value


def optional_line(params: dict[str, Any], key: str) -> int | None:
    """Return ``params[key]`` as a positive 1-based line number, or ``None``."""
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidParams(f"'{key}' must be a positive integer")
    return value


# ---------------------------------------------------------------------------
# Synchronous method table
# ---------------------------------------------------------------------------


def _call_find_symbol(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    return handlers.find_symbol(
        require_str(params, "name"),
        kind=optional_str(params, "kind"),
        repo_root=repo_root,
    )


def _call_find_callers(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    return handlers.find_callers(require_str(params, "symbol"), repo_root=repo_root)


def _call_find_callees(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    return handlers.find_callees(require_str(params, "symbol"), repo_root=repo_root)


def _call_find_imports(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    return handlers.find_imports(require_str(params, "module"), repo_root=repo_root)


def _call_trace(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    depth = optional_int(params, "depth", 3, maximum=MAX_DEPTH)
    return handlers.get_dependency_graph(require_str(params, "symbol"), depth, repo_root=repo_root)


def _call_impact(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    max_depth = optional_int(params, "max_depth", 3, maximum=MAX_DEPTH)
    return handlers.impact_analysis(require_str(params, "symbol"), max_depth, repo_root=repo_root)


def _call_analyze_hotspots(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    threshold = optional_int(params, "threshold", 10, minimum=0, maximum=100)
    limit = optional_int(params, "limit", 50, minimum=1, maximum=500)
    return handlers.analyze_hotspots(threshold, limit, repo_root=repo_root)


def _call_analyze_coupling(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    threshold = optional_int(params, "threshold", 5, minimum=0, maximum=1000)
    return handlers.analyze_coupling(threshold, repo_root=repo_root)


def _call_analyze_circular(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    max_cycles = optional_int(params, "max_cycles", 100, minimum=1, maximum=1000)
    find_all = optional_bool(params, "find_all", False)
    return handlers.analyze_circular(max_cycles, find_all, repo_root=repo_root)


def _call_index(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    repo = optional_str(params, "repo") or repo_root or get_latest_repo_root() or str(Path.cwd())
    return handlers.index_repo(repo, force=optional_bool(params, "force", False))


def _call_status(params: dict[str, Any], repo_root: str | None) -> dict[str, Any]:
    return handlers.status(repo_root=optional_str(params, "repo") or repo_root)


#: Non-streaming methods the daemon exposes.
SYNC_METHODS: dict[str, SyncHandler] = {
    "find_symbol": _call_find_symbol,
    "find_callers": _call_find_callers,
    "find_callees": _call_find_callees,
    "find_imports": _call_find_imports,
    "trace": _call_trace,
    "impact": _call_impact,
    "analyze_hotspots": _call_analyze_hotspots,
    "analyze_coupling": _call_analyze_coupling,
    "analyze_circular": _call_analyze_circular,
    "index": _call_index,
    "status": _call_status,
}

#: Methods answered with ``stream`` chunks followed by a ``done`` message.
STREAM_METHODS = frozenset({"query", "explain", "simplify"})


# ---------------------------------------------------------------------------
# Dispatch and the stdio run loop
# ---------------------------------------------------------------------------


class Daemon:
    """Dispatches one JSON-RPC request at a time onto the shared handlers."""

    def __init__(self, repo_root: str | None = None, verbose: bool = False) -> None:
        self.repo_root = repo_root
        self.verbose = verbose
        #: Cleared by ``shutdown`` so the run loop exits after the response.
        self.running = True

    def handle(self, request: Any, respond: Responder) -> None:
        """Handle one decoded request, replying through ``respond``."""
        if not isinstance(request, dict):
            respond(error_response(None, INVALID_REQUEST, "Request must be a JSON object"))
            return

        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str) or not method.strip():
            respond(
                error_response(request_id, INVALID_REQUEST, "'method' must be a non-empty string")
            )
            return

        params = request.get("params") or {}
        if not isinstance(params, dict):
            respond(error_response(request_id, INVALID_PARAMS, "'params' must be an object"))
            return

        started = time.perf_counter()
        try:
            self._dispatch(request_id, method.strip(), params, respond)
        finally:
            if self.verbose:
                elapsed_ms = (time.perf_counter() - started) * 1000
                print(f"[daemon] method={method} duration_ms={elapsed_ms:.1f}", file=sys.stderr)

    def _dispatch(
        self, request_id: Any, method: str, params: dict[str, Any], respond: Responder
    ) -> None:
        if method == "shutdown":
            respond({"id": request_id, "result": {"ok": True}})
            self.running = False
            return
        if method in STREAM_METHODS:
            self._handle_streaming(request_id, method, params, respond)
            return

        handler = SYNC_METHODS.get(method)
        if handler is None:
            respond(error_response(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}"))
            return
        try:
            result = handler(params, self.repo_root)
        except InvalidParams as exc:
            respond(error_response(request_id, INVALID_PARAMS, str(exc)))
        except ToolError as exc:
            respond(error_response(request_id, INTERNAL_ERROR, str(exc)))
        except Exception as exc:  # pragma: no cover - defensive
            respond(error_response(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
        else:
            respond({"id": request_id, "result": result})

    def _handle_streaming(
        self, request_id: Any, method: str, params: dict[str, Any], respond: Responder
    ) -> None:
        """Run a streaming method, emitting ``stream`` chunks as they arrive."""

        def on_token(token: str) -> None:
            respond({"id": request_id, "stream": token})

        try:
            final = asyncio.run(self._stream(method, params, on_token))
        except InvalidParams as exc:
            respond(error_response(request_id, INVALID_PARAMS, str(exc)))
        except ToolError as exc:
            respond(error_response(request_id, INTERNAL_ERROR, str(exc)))
        except Exception as exc:  # provider/network failure mid-stream
            respond(error_response(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
        else:
            respond({"id": request_id, "done": True, **final})

    async def _stream(
        self, method: str, params: dict[str, Any], on_token: handlers.TokenSink
    ) -> dict[str, Any]:
        if method == "query":
            return await handlers.stream_query(
                require_str(params, "question"),
                optional_int(params, "limit", 5, maximum=handlers.MAX_QUERY_LIMIT),
                repo_root=self.repo_root,
                on_token=on_token,
            )
        file_path = require_str(params, "file")
        line_start = optional_line(params, "start")
        line_end = optional_line(params, "end")
        return await handlers.stream_code_review(
            method,
            file_path,
            line_start,
            line_end,
            repo_root=self.repo_root,
            on_token=on_token,
        )


def detect_repo_root(explicit: str | None) -> str | None:
    """Resolve which repository a freshly started daemon serves.

    Precedence: ``--repo`` → cwd when it is a git checkout → the most recently
    indexed repository → cwd (handlers then ask the client to index it).
    """
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return str(cwd)
    return get_latest_repo_root() or str(cwd)


def process_line(line: str, daemon: Daemon, respond: Responder) -> None:
    """Handle one NDJSON line (parse errors reply with ``-32700``)."""
    stripped = line.strip()
    if not stripped:
        return
    try:
        request = json.loads(stripped)
    except json.JSONDecodeError as exc:
        respond(error_response(None, PARSE_ERROR, f"Invalid JSON: {exc}"))
        return
    daemon.handle(request, respond)


def run_daemon(
    repo_root: str | None = None,
    verbose: bool = False,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Serve JSON-RPC requests from ``stdin`` until ``shutdown`` or EOF."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    daemon = Daemon(repo_root=detect_repo_root(repo_root), verbose=verbose)

    def respond(message: dict[str, Any]) -> None:
        sink.write(json.dumps(message, default=str) + "\n")
        sink.flush()

    if verbose:
        print(
            f"[daemon] ready repo_root={daemon.repo_root} version={__version__}",
            file=sys.stderr,
        )

    for line in source:
        process_line(line, daemon, respond)
        if not daemon.running:
            break
    return 0
