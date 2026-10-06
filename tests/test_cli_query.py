"""End-to-end tests for the query CLI commands."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecompass.cli import main as cli_main
from codecompass.cli.main import app
from codecompass.indexer.indexer import Indexer


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the CLI console so long symbol names are not truncated."""
    monkeypatch.setattr(cli_main.console, "width", 200)


runner = CliRunner()

AUTH_PY = '''\
"""Auth module."""


def validate_token(token: str) -> bool:
    """Validate a token."""
    return bool(token)
'''

API_PY = '''\
"""API module."""

import os
from typing import Dict

from app.auth import validate_token


def login(username: str, password: str) -> bool:
    """Log a user in."""
    token = f"{username}:{password}"
    return validate_token(token)


def process(data: Dict) -> bool:
    """A complex function with many decision points."""
    result = False
    if data.get("a"):
        result = True
    elif data.get("b") and data.get("c"):
        result = True
    else:
        for i in range(10):
            if i > 5 or i < 2:
                result = not result
    return validate_token(str(result))
'''

MAIN_PY = '''\
"""Entry point."""

from app.api import login


def main() -> int:
    """Run the app."""
    if login("admin", "secret"):
        return 0
    return 1
'''

TEST_PY = '''\
"""Tests."""

from app.auth import validate_token


def test_validate_token():
    """Check validation."""
    assert validate_token("abc") is True
'''

CIRC_A_PY = '''\
"""Cycle part A."""

from circ_b import b_func


def a_func():
    """A."""
    return b_func()
'''

CIRC_B_PY = '''\
"""Cycle part B."""

from circ_a import a_func


def b_func():
    """B."""
    return a_func()
'''


@pytest.fixture
def indexed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Index a real sample repo into an isolated HOME."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "auth.py").write_text(AUTH_PY)
    (repo / "app" / "api.py").write_text(API_PY)
    (repo / "app" / "main.py").write_text(MAIN_PY)
    (repo / "circ_a.py").write_text(CIRC_A_PY)
    (repo / "circ_b.py").write_text(CIRC_B_PY)
    (repo / "tests" / "test_api.py").write_text(TEST_PY)

    Indexer(repo).index()
    return repo


def test_callers_cross_file(indexed_repo: Path) -> None:
    """Test that callers resolves across files (including tests)."""
    result = runner.invoke(app, ["callers", "validate_token"])
    assert result.exit_code == 0, result.output
    assert "login" in result.output
    assert "process" in result.output
    assert "test_validate_token" in result.output
    assert "3 caller(s)" in result.output


def test_callees(indexed_repo: Path) -> None:
    """Test callee lookup."""
    result = runner.invoke(app, ["callees", "login"])
    assert result.exit_code == 0, result.output
    assert "validate_token" in result.output


def test_trace(indexed_repo: Path) -> None:
    """Test the dependency tree."""
    result = runner.invoke(app, ["trace", "main", "--depth", "2"])
    assert result.exit_code == 0, result.output
    assert "main" in result.output
    assert "login" in result.output
    assert "validate_token" in result.output


def test_path(indexed_repo: Path) -> None:
    """Test shortest path between symbols."""
    result = runner.invoke(app, ["path", "main", "validate_token"])
    assert result.exit_code == 0, result.output
    assert "main" in result.output
    assert "login" in result.output
    assert "validate_token" in result.output


def test_path_no_path(indexed_repo: Path) -> None:
    """Test that a missing path is reported."""
    result = runner.invoke(app, ["path", "validate_token", "main"])
    assert result.exit_code == 0, result.output
    assert "No call path found" in result.output


def test_impact(indexed_repo: Path) -> None:
    """Test impact analysis."""
    result = runner.invoke(app, ["impact", "validate_token"])
    assert result.exit_code == 0, result.output
    assert "MEDIUM" in result.output
    assert "login" in result.output
    assert "test_api.py" in result.output


def test_analyze_hotspots(indexed_repo: Path) -> None:
    """Test complexity hotspots."""
    result = runner.invoke(app, ["analyze", "hotspots", "--threshold", "3"])
    assert result.exit_code == 0, result.output
    assert "process" in result.output


def test_analyze_coupling(indexed_repo: Path) -> None:
    """Test module coupling."""
    result = runner.invoke(app, ["analyze", "coupling", "--threshold", "2"])
    assert result.exit_code == 0, result.output
    assert "app/api.py" in result.output


def test_analyze_circular(indexed_repo: Path) -> None:
    """Test circular dependency detection."""
    result = runner.invoke(app, ["analyze", "circular"])
    assert result.exit_code == 0, result.output
    assert "Circular Dependencies" in result.output
    assert "circ_a.py" in result.output
    assert "circ_b.py" in result.output


def test_analyze_unknown_mode(indexed_repo: Path) -> None:
    """Test an unknown analysis mode."""
    result = runner.invoke(app, ["analyze", "bogus"])
    assert result.exit_code == 1
    assert "Unknown mode" in result.output


def test_query_requires_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that query commands explain when nothing is indexed."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = runner.invoke(app, ["callers", "foo"])
    assert result.exit_code == 1
    assert "No indexed repository found" in result.output

    result = runner.invoke(app, ["query", "anything"])
    assert result.exit_code == 1
    assert "No indexed repository found" in result.output


def test_query_returns_ranked_results(indexed_repo: Path) -> None:
    """Test the query command returns a results table with citations."""
    result = runner.invoke(app, ["query", "Where is token validation?", "--no-llm"])

    assert result.exit_code == 0, result.output
    assert "app/auth.py" in result.output
    assert "validate_token" in result.output
    assert "Score" in result.output
    assert "Top" in result.output and "results" in result.output


def test_query_verbose_shows_content(indexed_repo: Path) -> None:
    """Test --verbose prints full chunk content and score breakdown."""
    result = runner.invoke(app, ["query", "token validation", "--no-llm", "--verbose"])

    assert result.exit_code == 0, result.output
    assert "Content" in result.output
    assert "vector" in result.output and "keyword" in result.output


def test_query_limit(indexed_repo: Path) -> None:
    """Test --limit caps the number of results shown."""
    result = runner.invoke(app, ["query", "login user", "--limit", "2", "--no-llm"])

    assert result.exit_code == 0, result.output
    assert "Top 2 results" in result.output


def test_query_file_filter(indexed_repo: Path) -> None:
    """Test --file restricts results to that file."""
    result = runner.invoke(app, ["query", "login", "--file", "app/auth.py", "--no-llm"])

    assert result.exit_code == 0, result.output
    assert "app/auth.py" in result.output
    assert "app/api.py" not in result.output


def test_index_reports_chunks(indexed_repo: Path) -> None:
    """Test the index command reports how many chunks were created."""
    result = runner.invoke(app, ["index", str(indexed_repo)])

    assert result.exit_code == 0, result.output
    assert "Indexing Complete" in result.output
    assert "Chunks:" in result.output
