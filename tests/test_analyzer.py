"""Tests for static analysis (hotspots, coupling, circular)."""

from pathlib import Path

from codecompass.query.analyzer import Analyzer
from codecompass.query.graph import CodeGraph


def _analyzer(query_db: dict) -> Analyzer:
    return Analyzer(CodeGraph(query_db["session"], query_db["repo_root"]))


def test_hotspots(query_db: dict) -> None:
    """Test complexity hotspot detection."""
    analyzer = _analyzer(query_db)
    rows = analyzer.hotspots(threshold=5)

    assert len(rows) == 1
    assert rows[0]["name"] == "login"
    assert rows[0]["complexity"] == 6
    assert rows[0]["file"] == "app/services/login.py"

    # threshold above everything -> empty
    assert analyzer.hotspots(threshold=100) == []


def test_hotspots_ignore_classes(query_db: dict) -> None:
    """Test that classes (no complexity) are not reported."""
    analyzer = _analyzer(query_db)
    rows = analyzer.hotspots(threshold=0)
    assert all(row["kind"] in ("function", "method") for row in rows)


def test_coupling(query_db: dict) -> None:
    """Test module coupling detection."""
    analyzer = _analyzer(query_db)
    rows = analyzer.coupling(min_imports=2)

    by_file = {row["file"]: row for row in rows}
    row = by_file["app/services/login.py"]
    assert row["imports"] == 3
    assert row["imported_by"] == 1
    assert row["total"] == 4
    assert row["coupling"] == row["fan_in"] + row["fan_out"]


def test_circular(query_db: dict) -> None:
    """Test circular dependency detection."""
    analyzer = _analyzer(query_db)
    cycle = analyzer.circular()

    assert cycle is not None
    files = {f for edge in cycle for f in edge}
    assert files == {"circ/a.py", "circ/b.py"}


def test_circular_acyclic_graph(tmp_path: Path) -> None:
    """Test that an acyclic repo reports no cycle."""
    from datetime import datetime

    from codecompass.indexer.schema import File, Import, Symbol, get_session

    session = get_session(str(tmp_path / "acyclic.db"))
    repo_root = str(tmp_path / "repo")
    ts = datetime(2026, 1, 1)

    a = File(
        path=str(tmp_path / "a.py"),
        relative_path="a.py",
        language="python",
        hash="x",
        last_modified=ts,
        repo_root=repo_root,
    )
    b = File(
        path=str(tmp_path / "b.py"),
        relative_path="b.py",
        language="python",
        hash="x",
        last_modified=ts,
        repo_root=repo_root,
    )
    session.add_all([a, b])
    session.flush()
    a_sym = Symbol(name="a_func", kind="function", line_start=1, line_end=1, file_id=a.id)
    b_sym = Symbol(name="b_func", kind="function", line_start=1, line_end=1, file_id=b.id)
    session.add_all([a_sym, b_sym])
    session.flush()
    # a imports b (one direction only)
    session.add(
        Import(
            file_id=a.id, module="b", imported_name="b_func", is_default=0, is_from_import=1, line=1
        )
    )
    session.commit()

    analyzer = Analyzer(CodeGraph(session, repo_root))
    assert analyzer.circular() is None
