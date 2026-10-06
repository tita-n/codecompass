"""MCP server exposing CodeCompass intelligence to AI agents.

``handlers`` is dependency-light and can be imported directly; ``server``
(which pulls the MCP SDK) is imported lazily on attribute access.
"""

from typing import Any

from codecompass.mcp import handlers

__all__ = ["create_server", "handlers", "run_http", "run_stdio"]


def __getattr__(name: str) -> Any:
    """Import the MCP-SDK-dependent helpers lazily."""
    if name in {"create_server", "run_http", "run_stdio"}:
        from codecompass.mcp import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
