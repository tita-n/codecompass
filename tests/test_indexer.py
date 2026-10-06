"""Tests for the indexing engine."""

from pathlib import Path

import pytest

from codecompass.indexer.indexer import Indexer
from codecompass.indexer.languages import get_language_for_file, is_supported_file
from codecompass.indexer.parser import compute_file_hash, init_parsers, parse_python_file
from codecompass.indexer.schema import Call, File, Import, Symbol, get_session

SAMPLE_PY = '''\
"""Sample module docstring."""

import os
from datetime import datetime as dt


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def calculate(a: int, b: int, operation: str) -> int:
    """Calculate based on operation."""
    if operation == "add":
        return add(a, b)
    elif operation == "multiply":
        return multiply(a, b)
    else:
        raise ValueError(f"Unknown operation: {operation}")


class Calculator:
    """A simple calculator."""

    def __init__(self, name: str = "Calculator"):
        self.name = name

    def add(self, a: int, b: int) -> int:
        return multiply(a, b)

    def multiply(self, a: int, b: int) -> int:
        return a * b
'''


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a sample repository for testing."""
    repo_path = tmp_path / "sample_repo"
    repo_path.mkdir()
    (repo_path / "example.py").write_text(SAMPLE_PY)
    return repo_path


def test_is_supported_file() -> None:
    """Test file support detection."""
    assert is_supported_file(Path("test.py")) is True
    assert is_supported_file(Path("test.js")) is True
    assert is_supported_file(Path("test.ts")) is True
    assert is_supported_file(Path("test.java")) is True
    assert is_supported_file(Path("test.go")) is True
    assert is_supported_file(Path("test.rs")) is True
    assert is_supported_file(Path("test.php")) is True
    assert is_supported_file(Path("test.cpp")) is True
    assert is_supported_file(Path("test.cs")) is True
    assert is_supported_file(Path("test.txt")) is False
    assert is_supported_file(Path("test.md")) is False


@pytest.mark.parametrize(
    "ext,expected",
    [
        (".py", "python"),
        (".js", "javascript"),
        (".ts", "typescript"),
        (".java", "java"),
        (".go", "go"),
        (".rs", "rust"),
        (".php", "php"),
        (".cpp", "cpp"),
        (".cs", "c_sharp"),
    ],
)
def test_get_language_for_file(ext: str, expected: str) -> None:
    """Test language detection."""
    lang = get_language_for_file(Path(f"test{ext}"))
    assert lang is not None
    assert lang.name == expected


def test_get_language_for_file_unknown() -> None:
    """Test unknown extensions return None."""
    assert get_language_for_file(Path("test.xyz")) is None


def test_language_configs() -> None:
    """Test language registry contents."""
    from codecompass.indexer.languages import LANGUAGES

    assert LANGUAGES["python"].extensions == [".py"]


def test_init_parsers() -> None:
    """Test that the Python parser initializes."""
    parsers = init_parsers()
    assert "python" in parsers


def test_compute_file_hash(tmp_path: Path) -> None:
    """Test file hashing."""
    file_path = tmp_path / "hashme.py"
    file_path.write_text("def foo():\n    pass\n")
    first = compute_file_hash(file_path)
    second = compute_file_hash(file_path)
    assert first == second
    assert len(first) == 64  # SHA256 hex digest


def test_extract_symbols_from_python() -> None:
    """Test Python symbol extraction."""
    parsers = init_parsers()
    symbols, _, _ = parse_python_file(Path("example.py"), SAMPLE_PY, parsers["python"])

    assert len(symbols) == 7
    functions = [s for s in symbols if s["kind"] == "function"]
    methods = [s for s in symbols if s["kind"] == "method"]
    classes = [s for s in symbols if s["kind"] == "class"]
    assert {s["name"] for s in functions} == {"add", "multiply", "calculate"}
    assert {s["name"] for s in methods} == {"add", "multiply", "__init__"}
    assert [s["name"] for s in classes] == ["Calculator"]

    add_fn = next(s for s in functions if s["name"] == "add")
    assert add_fn["docstring"] == "Add two numbers."
    assert add_fn["signature"] == "(a: int, b: int)"

    init_method = next(s for s in methods if s["name"] == "__init__")
    assert init_method["parent"] == "Calculator"

    calculate = next(s for s in functions if s["name"] == "calculate")
    assert calculate["line_start"] >= 1
    assert calculate["line_end"] >= calculate["line_start"]


def test_extract_calls_from_python() -> None:
    """Test Python call extraction."""
    parsers = init_parsers()
    _, calls, _ = parse_python_file(Path("example.py"), SAMPLE_PY, parsers["python"])

    pairs = {(c["caller"], c["callee"]) for c in calls}
    assert ("calculate", "add") in pairs
    assert ("calculate", "multiply") in pairs
    # Calculator.add calls the module-level multiply
    assert ("add", "multiply") in pairs
    assert all(c["line"] >= 1 for c in calls)
    assert all(c["call_text"] for c in calls)


def test_extract_imports_from_python() -> None:
    """Test Python import extraction."""
    parsers = init_parsers()
    _, _, imports = parse_python_file(Path("example.py"), SAMPLE_PY, parsers["python"])

    by_module = {i["module"]: i for i in imports}
    assert by_module["os"]["imported_name"] is None
    assert by_module["os"]["is_from_import"] == 0
    assert by_module["datetime"]["imported_name"] == "datetime"
    assert by_module["datetime"]["alias"] == "dt"
    assert by_module["datetime"]["is_from_import"] == 1


def test_indexer_collect_files(sample_repo: Path) -> None:
    """Test file collection."""
    indexer = Indexer(sample_repo)
    files = indexer._collect_files()

    # Should find the Python file
    assert len(files) >= 1
    assert any(f.name == "example.py" for f in files)


@pytest.mark.integration
def test_indexer_index(sample_repo: Path, tmp_path: Path) -> None:
    """Test full indexing pipeline."""
    indexer = Indexer(sample_repo, db_path=tmp_path / "index.db")
    stats = indexer.index()

    assert stats["files"] == 1
    assert stats["symbols"] == 7
    assert stats["calls"] == 4  # 3 intra-file + 1 unresolved (ValueError)
    assert stats["imports"] == 2
    assert stats["duration_seconds"] >= 0

    # Verify database contents
    session = get_session(str(tmp_path / "index.db"))
    assert session.query(File).count() == 1
    assert session.query(Symbol).count() == 7
    assert session.query(Import).count() == 2

    calls = session.query(Call).all()
    pairs = {(c.caller.name, c.callee.name) for c in calls if c.callee is not None}
    assert ("calculate", "add") in pairs
    assert ("calculate", "multiply") in pairs
    # Unresolved (external) calls are kept with a NULL callee for later resolution
    assert any(c.callee_id is None and c.callee_name == "ValueError" for c in calls)

    # Method parentage stored correctly
    init_symbol = session.query(Symbol).filter_by(name="__init__").first()
    assert init_symbol is not None
    assert init_symbol.parent is not None
    assert init_symbol.parent.name == "Calculator"


@pytest.mark.integration
def test_indexer_incremental(sample_repo: Path, tmp_path: Path) -> None:
    """Test incremental indexing only processes changed files."""
    db_path = tmp_path / "index.db"

    Indexer(sample_repo, db_path=db_path).index()

    # Re-index without changes: nothing to do
    stats = Indexer(sample_repo, db_path=db_path).index()
    assert stats["files"] == 0
    assert stats["symbols"] == 0

    # Modify the file: the changed file is re-indexed in full
    example = sample_repo / "example.py"
    example.write_text(SAMPLE_PY + "\n\ndef square(x: int) -> int:\n    return x * x\n")

    stats = Indexer(sample_repo, db_path=db_path).index()
    assert stats["files"] == 1
    assert stats["symbols"] == 8  # whole changed file is re-extracted

    session = get_session(str(db_path))
    names = {s.name for s in session.query(Symbol).all()}
    assert "square" in names
    assert session.query(Symbol).count() == 8


@pytest.mark.integration
def test_indexer_force_reindex(sample_repo: Path, tmp_path: Path) -> None:
    """Test --force re-indexes all files without duplicating data."""
    db_path = tmp_path / "index.db"

    Indexer(sample_repo, db_path=db_path).index()
    stats = Indexer(sample_repo, db_path=db_path).index(force=True)

    assert stats["files"] == 1
    assert stats["symbols"] == 7

    session = get_session(str(db_path))
    assert session.query(File).count() == 1
    assert session.query(Symbol).count() == 7


@pytest.mark.integration
def test_indexer_removes_deleted_files(sample_repo: Path, tmp_path: Path) -> None:
    """Test that deleted files are removed from the index."""
    db_path = tmp_path / "index.db"

    Indexer(sample_repo, db_path=db_path).index()
    (sample_repo / "example.py").unlink()

    Indexer(sample_repo, db_path=db_path).index()

    session = get_session(str(db_path))
    assert session.query(File).count() == 0
    assert session.query(Symbol).count() == 0
    assert session.query(Call).count() == 0
    assert session.query(Import).count() == 0


@pytest.mark.integration
def test_indexer_multiple_files(sample_repo: Path, tmp_path: Path) -> None:
    """Test indexing a repo with multiple files and ignored directories."""
    (sample_repo / "lib.py").write_text("def helper() -> None:\n    return None\n")
    (sample_repo / "node_modules").mkdir()
    (sample_repo / "node_modules" / "dep.js").write_text("console.log('ignored')\n")
    (sample_repo / "notes.txt").write_text("not code\n")

    indexer = Indexer(sample_repo, db_path=tmp_path / "index.db")
    stats = indexer.index()

    assert stats["files"] == 2
    session = get_session(str(tmp_path / "index.db"))
    assert session.query(File).count() == 2
    assert "helper" in {s.name for s in session.query(Symbol).all()}
