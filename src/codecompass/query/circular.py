"""Circular dependency detection over the module (file) graph."""

import networkx as nx

from codecompass.query.graph import CodeGraph


class CircularAnalyzer:
    """Find import cycles between indexed files.

    The default path is bounded and fast: ``simple_cycles`` with a short
    ``length_bound`` catches tight cycles without enumerating an exponential
    cycle space on dense repos. ``find_all_cycles`` (``--all``) opts into the
    exhaustive, potentially slow search.
    """

    def __init__(self, graph: CodeGraph):
        self.graph = graph

    def find_cycles(self, max_cycles: int = 100, length_bound: int = 5) -> list[list[str]]:
        """Return up to ``max_cycles`` short cycles, normalised and deduplicated.

        Each cycle is rotated so the lexicographically smallest node comes
        first; ``A -> B -> A`` and ``B -> A -> B`` therefore collapse to one.
        """
        return self._collect(max_cycles=max_cycles, length_bound=length_bound)

    def find_all_cycles(self, max_cycles: int = 100) -> list[list[str]]:
        """Exhaustively enumerate cycles (slow on dense graphs; ``--all``)."""
        return self._collect(max_cycles=max_cycles, length_bound=None)

    def _collect(self, max_cycles: int, length_bound: int | None) -> list[list[str]]:
        module_graph = self.graph.module_graph
        cycles: list[list[str]] = []
        try:
            if length_bound is None:
                iterator = nx.simple_cycles(module_graph)
            else:
                iterator = nx.simple_cycles(module_graph, length_bound=length_bound)
            for cycle in iterator:
                if len(cycles) >= max_cycles:
                    break
                cycles.append(self._normalise(list(cycle)))
        except nx.NetworkXNoCycle:
            pass
        return self._dedupe(cycles)

    @staticmethod
    def _normalise(cycle: list[str]) -> list[str]:
        """Rotate a cycle so the smallest node is first (stable identity)."""
        if not cycle:
            return cycle
        idx = cycle.index(min(cycle))
        return cycle[idx:] + cycle[:idx]

    @staticmethod
    def _dedupe(cycles: list[list[str]]) -> list[list[str]]:
        """Drop rotations/duplicates of the same node set."""
        seen: set[tuple[str, ...]] = set()
        unique: list[list[str]] = []
        for cycle in cycles:
            key = tuple(sorted(cycle))
            if key not in seen:
                seen.add(key)
                unique.append(cycle)
        return unique
