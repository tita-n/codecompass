"""Backwards-compatible re-export of the shared handler layer.

The implementation lives in :mod:`codecompass.api.handlers` so the MCP server
and the VS Code daemon share one source of truth; this module keeps the public
names importable from their historical location (``codecompass.mcp.handlers``).
"""

from codecompass.api.handlers import HANDLERS as HANDLERS
from codecompass.api.handlers import MAX_DEPTH as MAX_DEPTH
from codecompass.api.handlers import ToolError as ToolError
from codecompass.api.handlers import analyze_circular as analyze_circular
from codecompass.api.handlers import analyze_coupling as analyze_coupling
from codecompass.api.handlers import analyze_hotspots as analyze_hotspots
from codecompass.api.handlers import find_callees as find_callees
from codecompass.api.handlers import find_callers as find_callers
from codecompass.api.handlers import find_imports as find_imports
from codecompass.api.handlers import find_symbol as find_symbol
from codecompass.api.handlers import get_dependency_graph as get_dependency_graph
from codecompass.api.handlers import impact_analysis as impact_analysis
from codecompass.api.handlers import load_graph as load_graph

__all__ = [
    "HANDLERS",
    "MAX_DEPTH",
    "ToolError",
    "analyze_circular",
    "analyze_coupling",
    "analyze_hotspots",
    "find_callers",
    "find_callees",
    "find_imports",
    "find_symbol",
    "get_dependency_graph",
    "impact_analysis",
    "load_graph",
]
