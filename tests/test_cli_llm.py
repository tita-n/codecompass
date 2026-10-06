"""End-to-end tests for the LLM-backed CLI commands (offline, fake provider)."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecompass.cli import main as cli_main
from codecompass.cli.main import app
from codecompass.indexer.indexer import Indexer
from codecompass.llm.base import LLMClient
from tests.conftest import FakeLLMClient

ANSWER = "Payment validation happens in validate_payment [app/auth.py:4-8]."

AUTH_PY = '''\
"""Auth module."""


def validate_token(token: str) -> bool:
    """Validate a token."""
    return bool(token)
'''

API_PY = '''\
"""API module."""


def login(username: str, password: str) -> bool:
    """Log a user in."""
    return validate_token(f"{username}:{password}")


def validate_token(token: str) -> bool:
    """Validate a token."""
    return bool(token)
'''


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the console so table cells are not truncated."""
    monkeypatch.setattr(cli_main.console, "width", 200)


@pytest.fixture
def indexed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Index a tiny repo into an isolated HOME."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "auth.py").write_text(AUTH_PY)
    (repo / "app" / "api.py").write_text(API_PY)
    Indexer(repo).index()
    return repo


@pytest.fixture
def fake_llm(
    install_fake_llm: Callable[[FakeLLMClient], FakeLLMClient],
) -> FakeLLMClient:
    """Install a deterministic fake LLM provider."""
    return install_fake_llm(FakeLLMClient(ANSWER))


runner = CliRunner()


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def test_query_streams_answer_with_citations(indexed_repo: Path, fake_llm: FakeLLMClient) -> None:
    """Test the LLM answer is streamed and followed by a sources table."""
    result = runner.invoke(app, ["query", "where is token validation?"])

    assert result.exit_code == 0, result.output
    assert "Payment validation happens in" in result.output
    assert "Sources" in result.output
    assert "app/auth.py" in result.output
    assert "validate_token" in result.output
    # The provider actually received the retrieved context.
    assert "### File: app/auth.py" in fake_llm.calls[0]["user"]


def test_query_no_llm_returns_chunks(indexed_repo: Path) -> None:
    """Test --no-llm preserves the retrieval-only output (Phase 3 behaviour)."""
    result = runner.invoke(app, ["query", "where is token validation?", "--no-llm"])

    assert result.exit_code == 0, result.output
    assert "--no-llm" in result.output
    assert "Top 5 results" in result.output
    assert "app/auth.py" in result.output
    assert "Score" in result.output


def test_query_raw_emits_json(indexed_repo: Path, fake_llm: FakeLLMClient) -> None:
    """Test --raw outputs parseable JSON with answer and citations."""
    result = runner.invoke(app, ["query", "token validation", "--raw"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["answer"].strip() == ANSWER.strip()
    assert payload["citations"][0]["file_path"].startswith("app/")
    assert payload["chunks"][0]["content"]
    assert "Top 5 results" not in result.output  # no table in raw mode


def test_query_falls_back_to_chunks_on_provider_error(
    indexed_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test a provider failure degrades to raw chunks with a warning."""

    def _boom(config: object) -> LLMClient:
        raise RuntimeError("provider offline")

    monkeypatch.setattr("codecompass.llm.factory.create_llm_client", _boom)

    result = runner.invoke(app, ["query", "token validation"])

    assert result.exit_code == 0, result.output
    assert "LLM answer unavailable" in result.output
    assert "provider offline" in result.output
    assert "Falling back to the retrieved chunks" in result.output
    assert "app/auth.py" in result.output


def test_query_raw_reports_provider_error_as_json(
    indexed_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test --raw keeps output machine-readable even when the LLM fails."""

    def _boom(config: object) -> LLMClient:
        raise RuntimeError("provider offline")

    monkeypatch.setattr("codecompass.llm.factory.create_llm_client", _boom)

    result = runner.invoke(app, ["query", "token validation", "--raw"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["answer"] is None
    assert payload["error"] == "provider offline"
    assert payload["chunks"]


def test_query_requires_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test query still refuses to run without an index."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = runner.invoke(app, ["query", "anything"])

    assert result.exit_code == 1
    assert "No indexed repository found" in result.output


# ---------------------------------------------------------------------------
# explain / simplify
# ---------------------------------------------------------------------------


def test_explain_streams_explanation(indexed_repo: Path, fake_llm: FakeLLMClient) -> None:
    """Test explain streams an explanation and sends the requested slice."""
    target = indexed_repo / "app" / "auth.py"
    result = runner.invoke(app, ["explain", str(target), "--start", "4", "--end", "6"])

    assert result.exit_code == 0, result.output
    assert "Payment validation happens in" in result.output
    call = fake_llm.calls[0]
    assert "def validate_token(token: str) -> bool:" in call["user"]
    assert "lines 4-6" in call["user"]


def test_simplify_is_read_only_preview(indexed_repo: Path, fake_llm: FakeLLMClient) -> None:
    """Test simplify shows the read-only banner and never writes files."""
    target = indexed_repo / "app" / "auth.py"
    before = target.read_text()

    result = runner.invoke(app, ["simplify", str(target), "--start", "4", "--end", "6"])

    assert result.exit_code == 0, result.output
    assert "Read-only preview" in result.output
    assert target.read_text() == before


def test_explain_defaults_to_whole_file(indexed_repo: Path, fake_llm: FakeLLMClient) -> None:
    """Test omitting --start/--end explains the entire file."""
    target = indexed_repo / "app" / "auth.py"
    result = runner.invoke(app, ["explain", str(target)])

    assert result.exit_code == 0, result.output
    assert '"""Auth module."""' in fake_llm.calls[0]["user"]


def test_explain_missing_file(indexed_repo: Path) -> None:
    """Test a missing file is reported clearly."""
    result = runner.invoke(app, ["explain", str(indexed_repo / "nope.py")])

    assert result.exit_code == 1
    assert "File not found" in result.output


def test_explain_invalid_range(indexed_repo: Path) -> None:
    """Test an out-of-bounds line range is rejected."""
    result = runner.invoke(
        app, ["explain", str(indexed_repo / "app" / "auth.py"), "--start", "1", "--end", "999"]
    )

    assert result.exit_code == 1
    assert "Invalid range" in result.output


def test_explain_with_llm_disabled(indexed_repo: Path) -> None:
    """Test retrieval-only mode refuses to explain and shows the code instead."""
    from codecompass.utils.config import get_config

    config_path = get_config().config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("llm:\n  provider: none\n")

    result = runner.invoke(
        app, ["explain", str(indexed_repo / "app" / "auth.py"), "--start", "4", "--end", "6"]
    )

    assert result.exit_code == 1
    assert "LLM is disabled" in result.output
    assert "validate_token" in result.output  # the code itself is shown


# ---------------------------------------------------------------------------
# config set-key
# ---------------------------------------------------------------------------


def test_config_set_key_stores_in_keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test set-key stores the secret in the keyring, never in config.yaml."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, user, password: stored.__setitem__(f"{service}:{user}", password),
    )

    result = runner.invoke(app, ["config", "set-key", "anthropic", "--key", "sk-secret"])

    assert result.exit_code == 0, result.output
    assert "Stored API key for anthropic" in result.output
    assert stored == {"codecompass:anthropic:api_key": "sk-secret"}


def test_config_set_key_rejects_unknown_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test an unsupported provider is rejected."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = runner.invoke(app, ["config", "set-key", "ollama", "--key", "x"])

    assert result.exit_code == 1
    assert "Cannot store keys" in result.output
