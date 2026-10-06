"""Query engine for CodeCompass."""

from codecompass.query.analyzer import Analyzer
from codecompass.query.circular import CircularAnalyzer
from codecompass.query.coupling import CouplingAnalyzer, ModuleCoupling
from codecompass.query.graph import CodeGraph, get_graph
from codecompass.query.hotspots import Hotspot, HotspotAnalyzer
from codecompass.query.resolver import SymbolResolver, module_path_from_file

__all__ = [
    "Analyzer",
    "CircularAnalyzer",
    "CodeGraph",
    "CouplingAnalyzer",
    "Hotspot",
    "HotspotAnalyzer",
    "ModuleCoupling",
    "SymbolResolver",
    "get_graph",
    "module_path_from_file",
]
