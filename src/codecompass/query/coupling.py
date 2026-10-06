"""Module coupling analysis: fan-in / fan-out / instability per file."""

from dataclasses import asdict, dataclass

from codecompass.query.graph import CodeGraph


@dataclass
class ModuleCoupling:
    """Coupling metrics for one module (file)."""

    path: str
    fan_in: int  # files that import this module
    fan_out: int  # modules this file imports
    instability: float  # fan_out / (fan_in + fan_out); 0 = stable, 1 = unstable
    coupling: int  # fan_in + fan_out

    def to_dict(self) -> dict:
        """Return a JSON-serialisable view (used by ``--json`` and MCP)."""
        return asdict(self)


class CouplingAnalyzer:
    """Rank modules by total coupling, computed from the module graph."""

    def __init__(self, graph: CodeGraph):
        self.graph = graph

    def analyze(self, min_coupling: int = 5) -> list[ModuleCoupling]:
        """Return modules with coupling >= ``min_coupling``, worst first."""
        module_graph = self.graph.module_graph
        imported_by: dict[str, int] = {}
        for _source, target in module_graph.edges():
            imported_by[target] = imported_by.get(target, 0) + 1

        files = set(self.graph.file_import_counts) | set(imported_by) | set(module_graph.nodes)
        results: list[ModuleCoupling] = []
        for node in files:
            fan_in = module_graph.in_degree(node) if module_graph.has_node(node) else 0
            if node not in module_graph.nodes:
                # A file known only via raw import counts (its targets did not
                # resolve to indexed files): trust the counts we have.
                fan_in = imported_by.get(node, 0)
            fan_out = self.graph.file_import_counts.get(node, 0)
            if module_graph.has_node(node):
                fan_out = max(fan_out, module_graph.out_degree(node))
            coupling = fan_in + fan_out
            if coupling < min_coupling:
                continue
            denom = fan_in + fan_out
            instability = (fan_out / denom) if denom > 0 else 0.0
            results.append(
                ModuleCoupling(
                    path=node,
                    fan_in=fan_in,
                    fan_out=fan_out,
                    instability=round(instability, 3),
                    coupling=coupling,
                )
            )
        results.sort(key=lambda m: m.coupling, reverse=True)
        return results
