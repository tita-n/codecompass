"""Static analysis over the indexed codebase: hotspots, coupling, cycles.

This module is the back-compatible façade over the dedicated analyzers in
:mod:`codecompass.query.hotspots`, :mod:`codecompass.query.coupling` and
:mod:`codecompass.query.circular`. The ``Analyzer`` methods below keep their
historic dict-based shapes (so the CLI, daemon handlers and existing tests
keep working); new code should prefer the dataclass-returning analyzers
directly.
"""

from codecompass.query.circular import CircularAnalyzer
from codecompass.query.coupling import CouplingAnalyzer
from codecompass.query.graph import CodeGraph
from codecompass.query.hotspots import HotspotAnalyzer


class Analyzer:
    """High-level codebase analysis built on top of the call graph."""

    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.hotspot_analyzer = HotspotAnalyzer(graph.session)
        self.coupling_analyzer = CouplingAnalyzer(graph)
        self.circular_analyzer = CircularAnalyzer(graph)

    def hotspots(self, threshold: int = 10, limit: int = 50) -> list[dict]:
        """Return functions/methods with complexity >= ``threshold`` (worst first)."""
        return [h.to_dict() for h in self.hotspot_analyzer.find(threshold, limit)]

    def coupling(self, min_imports: int = 5) -> list[dict]:
        """Return files with (fan-in + fan-out) >= ``min_imports`` (worst first)."""
        rows: list[dict] = []
        for module in self.coupling_analyzer.analyze(min_imports):
            rows.append(
                {
                    "file": module.path,
                    "imports": module.fan_out,
                    "imported_by": module.fan_in,
                    "fan_in": module.fan_in,
                    "fan_out": module.fan_out,
                    "instability": module.instability,
                    "total": module.coupling,
                    "coupling": module.coupling,
                }
            )
        return rows

    def circular(self, max_cycles: int = 100, length_bound: int = 5) -> list[tuple[str, str]] | None:
        """Return the first import cycle as ``(from, to)`` edges, or ``None``.

        Keeps the historic edge-list shape consumed by the CLI and daemon.
        Use :meth:`cycles` for the full normalised node-cycle list.
        """
        cycles = self.cycles(max_cycles=max_cycles, length_bound=length_bound)
        if not cycles:
            return None
        nodes = cycles[0]
        return [(nodes[i], nodes[(i + 1) % len(nodes)]) for i in range(len(nodes))]

    def cycles(
        self, max_cycles: int = 100, length_bound: int = 5, find_all: bool = False
    ) -> list[list[str]]:
        """Return normalised import cycles (empty when the graph is acyclic)."""
        if find_all:
            return self.circular_analyzer.find_all_cycles(max_cycles)
        return self.circular_analyzer.find_cycles(max_cycles, length_bound)

    def circular_edges(self) -> list[tuple[str, str]] | None:
        """Alias for :meth:`circular` (legacy name)."""
        return self.circular()

