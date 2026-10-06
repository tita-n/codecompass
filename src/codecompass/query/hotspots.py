"""Complexity hotspot analysis: functions ranked by cyclomatic complexity."""

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from codecompass.indexer.schema import Symbol


@dataclass
class Hotspot:
    """A function or method whose complexity meets the query threshold."""

    name: str
    kind: str
    file: str
    line_start: int
    line_end: int
    complexity: int
    size: int  # lines of code

    def to_dict(self) -> dict:
        """Return a JSON-serialisable view (used by ``--json`` and MCP)."""
        return asdict(self)


class HotspotAnalyzer:
    """Find functions/methods with complexity >= threshold, ranked worst first."""

    def __init__(self, session: Session):
        self.session = session

    def find(self, threshold: int = 10, limit: int = 50) -> list[Hotspot]:
        """Return hotspots ordered by complexity descending, then size."""
        rows = (
            self.session.query(Symbol)
            .filter(Symbol.complexity >= threshold)
            .filter(Symbol.kind.in_(["function", "method"]))
            .order_by(Symbol.complexity.desc())
            .limit(limit)
            .all()
        )
        results = [
            Hotspot(
                name=sym.name,
                kind=sym.kind,
                file=sym.file.relative_path if sym.file else "?",
                line_start=sym.line_start,
                line_end=sym.line_end,
                complexity=sym.complexity or 0,
                size=(sym.line_end - sym.line_start + 1),
            )
            for sym in rows
        ]
        results.sort(key=lambda h: (h.complexity, h.size), reverse=True)
        return results
