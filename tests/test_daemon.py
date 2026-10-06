"""Tests for the JSON-RPC daemon: dispatch, streaming, errors, stdio loop."""

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from codecompass import __version__
from codecompass.api import handlers
from codecompass.api.daemon import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Daemon,
    detect_repo_root,
    process_line,
    run_daemon,
)
from codecompass.indexer.indexer import Indexer

PAYMENTS_PY = '''\
"""Payments module."""


def validate_payment(amount: float, currency: str) -> bool:
    """Validate a payment amount and currency before charging."""
    if amount <= 0:
        return False
    if currency not in ("USD", "EUR", "GBP"):
        return False
    return amount < 10000


def process_payment(card_token: str, amount: float) -> str:
    """Charge a credit card using a stored card token."""
    return f"charged {amount} via {card_token}"
'''

CART_PY = '''\
"""Shopping cart helpers."""

from app.payments import validate_payment


def checkout(items: list, amount: float) -> bool:
    """Check out a cart after validating the payment."""
    if not items:
        return False
    return validate_payment(amount, "USD")
'''

TEST_PAYMENTS_PY = '''\
"""Tests for payments."""

from app.payments import process_payment, validate_payment


def test_validate_payment() -> None:
    """Negative amounts are rejected."""
    assert validate_payment(-1, "USD") is False


def test_process_payment() -> None:
    """Charging a card returns a receipt."""
    assert "charged" in process_payment("tok_123", 10.0)
'''

CIRC_A_PY = '''\
"""Cycle part A."""

from circ_b import b_func


def a_func() -> int:
    """Call B."""
    return b_func()
'''

CIRC_B_PY = '''\
"""Cycle part B."""

from circ_a import a_func


def b_func() -> int:
    """Call A."""
    return a_func()
'''

SAMPLE_FILES = (
    ("app/payments.py", PAYMENTS_PY),
    ("app/cart.py", CART_PY),
    ("tests/test_payments.py", TEST_PAYMENTS_PY),
    ("circ_a.py", CIRC_A_PY),
    ("circ_b.py", CIRC_B_PY),
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Index a sample repo into an isolated HOME; return its root."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    for relative, content in SAMPLE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    Indexer(root).index()
    return str(root)


def call(
    daemon: Daemon, method: str, params: dict[str, Any] | None = None, request_id: int = 1
) -> list[dict[str, Any]]:
    """Send one request and collect every response message."""
    messages: list[dict[str, Any]] = []
    daemon.handle({"id": request_id, "method": method, "params": params or {}}, messages.append)
    return messages


# ---------------------------------------------------------------------------
# Synchronous dispatch
# ---------------------------------------------------------------------------


def test_find_symbol_roundtrip(repo: str) -> None:
    """A find_symbol request returns the shared handler's schema."""
    messages = call(Daemon(repo_root=repo), "find_symbol", {"name": "validate_payment"})

    assert len(messages) == 1
    message = messages[0]
    assert message["id"] == 1
    assert "result" in message and "error" not in message
    result = message["result"]
    assert result["found"] is True
    assert result["count"] == 1
    assert result["symbols"][0]["file"] == "app/payments.py"
    assert result["symbols"][0]["line_start"] == 4


def test_find_callers_counts_cross_file_callers(repo: str) -> None:
    """find_callers sees cart.checkout and the test file."""
    messages = call(Daemon(repo_root=repo), "find_callers", {"symbol": "validate_payment"})

    result = messages[0]["result"]
    assert result["count"] >= 2
    files = {entry["file"] for entry in result["callers"]}
    assert "app/cart.py" in files
    assert "tests/test_payments.py" in files


def test_trace_returns_graph_payload(repo: str) -> None:
    """trace exposes flat nodes/edges for the graph view."""
    messages = call(Daemon(repo_root=repo), "trace", {"symbol": "checkout", "depth": 2})

    result = messages[0]["result"]
    assert result["found"] is True
    assert result["depth"] == 2
    assert result["node_count"] >= 2  # checkout -> validate_payment
    names = {node["name"] for node in result["nodes"]}
    assert {"checkout", "validate_payment"} <= names
    assert result["edge_count"] >= 1


def test_trace_clamps_absurd_depth(repo: str) -> None:
    """Depth is clamped to MAX_DEPTH instead of failing."""
    messages = call(Daemon(repo_root=repo), "trace", {"symbol": "checkout", "depth": 9999})

    assert messages[0]["result"]["depth"] <= 10


def test_impact_analysis_reports_risk(repo: str) -> None:
    """impact uses the same risk heuristic as the CLI."""
    messages = call(Daemon(repo_root=repo), "impact", {"symbol": "validate_payment"})

    result = messages[0]["result"]
    assert result["found"] is True
    assert result["risk"] in {"LOW", "MEDIUM", "HIGH"}
    assert result["direct_callers"] >= 1
    assert "app/payments.py" in result["affected_files"]


def test_unknown_method_is_method_not_found() -> None:
    """Unknown methods answer -32601 and echo the request id."""
    messages = call(Daemon(repo_root=None), "does_not_exist", {}, request_id=7)

    assert messages[0]["id"] == 7
    assert messages[0]["error"]["code"] == METHOD_NOT_FOUND
    assert "does_not_exist" in messages[0]["error"]["message"]


def test_missing_required_param_is_invalid_params() -> None:
    """A missing 'name' is -32602, not an internal crash."""
    messages = call(Daemon(repo_root=None), "find_symbol", {})

    assert messages[0]["error"]["code"] == INVALID_PARAMS
    assert "name" in messages[0]["error"]["message"]


def test_wrong_param_type_is_invalid_params(repo: str) -> None:
    """Non-integer depth is rejected with -32602."""
    messages = call(Daemon(repo_root=repo), "trace", {"symbol": "checkout", "depth": "deep"})

    assert messages[0]["error"]["code"] == INVALID_PARAMS


def test_non_object_request_is_invalid_request() -> None:
    """A non-object request is a -32600 protocol violation."""
    messages: list[dict[str, Any]] = []
    Daemon().handle(["not", "an", "object"], messages.append)

    assert messages[0]["error"]["code"] == INVALID_REQUEST
    assert messages[0]["id"] is None


def test_missing_method_is_invalid_request() -> None:
    """A request without 'method' is rejected before dispatch."""
    messages: list[dict[str, Any]] = []
    Daemon().handle({"id": 4}, messages.append)

    assert messages[0]["id"] == 4
    assert messages[0]["error"]["code"] == INVALID_REQUEST


def test_unindexed_repo_reports_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Using an unindexed repo root points the client at 'codecompass index'."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "unindexed").mkdir()
    messages = call(Daemon(repo_root=str(tmp_path / "unindexed")), "find_symbol", {"name": "x"})

    assert messages[0]["error"]["code"] == INTERNAL_ERROR
    assert "is not indexed" in messages[0]["error"]["message"]


# ---------------------------------------------------------------------------
# Streaming (query / explain / simplify)
# ---------------------------------------------------------------------------


class StubOrchestrator:
    """Stands in for the real Orchestrator: deterministic, offline, fast."""

    def __init__(self, tokens: list[str] | None = None) -> None:
        self.tokens = tokens if tokens is not None else ["Auth ", "lives ", "in payments. "]
        self.calls: list[dict[str, Any]] = []

    async def answer_question(
        self, question: str, chunks: list[dict[str, Any]]
    ) -> AsyncIterator[str]:
        self.calls.append({"method": "answer_question", "question": question})
        for token in self.tokens:
            yield token

    def explain(
        self, code: str, file_path: str, line_start: int, line_end: int, language: str = "text"
    ) -> AsyncIterator[str]:
        self.calls.append(
            {"method": "explain", "code": code, "file": file_path, "lines": (line_start, line_end)}
        )
        return self._stream()

    def simplify(
        self, code: str, file_path: str, line_start: int, line_end: int, language: str = "text"
    ) -> AsyncIterator[str]:
        self.calls.append(
            {"method": "simplify", "code": code, "file": file_path, "lines": (line_start, line_end)}
        )
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        for token in self.tokens:
            yield token


def install_stub(monkeypatch: pytest.MonkeyPatch) -> StubOrchestrator:
    """Patch the orchestrator factory with an offline stub."""
    stub = StubOrchestrator()
    monkeypatch.setattr(handlers, "build_orchestrator", lambda: stub)
    return stub


def test_query_streams_tokens_then_done(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """query emits stream chunks before the done payload, all with one id."""
    stub = install_stub(monkeypatch)
    messages = call(
        Daemon(repo_root=repo), "query", {"question": "Where is validate_payment defined?"}
    )

    streams = [message for message in messages if "stream" in message]
    done = messages[-1]
    assert streams
    assert done is messages[-1]
    assert done["done"] is True
    assert all(message["id"] == 1 for message in messages)
    assert "".join(message["stream"] for message in streams) == "".join(stub.tokens)
    assert done["count"] >= 1
    assert done["sources"][0]["file"]
    assert stub.calls and stub.calls[0]["method"] == "answer_question"


def test_query_without_context_skips_the_llm(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero retrieval hits answer a note without invoking the provider."""
    monkeypatch.setattr(handlers, "hybrid_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        handlers, "build_orchestrator", lambda: pytest.fail("LLM must not be built")
    )

    messages = call(Daemon(repo_root=repo), "query", {"question": "anything"})

    assert len(messages) == 2
    assert "No relevant context" in messages[0]["stream"]
    assert messages[1]["done"] is True
    assert messages[1]["count"] == 0
    assert messages[1]["sources"] == []


def test_query_without_index_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Querying with no index anywhere is an actionable internal error."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    messages = call(Daemon(repo_root=None), "query", {"question": "anything"})

    assert messages[0]["error"]["code"] == INTERNAL_ERROR
    assert "No indexed repository" in messages[0]["error"]["message"]


def test_query_requires_a_question(repo: str) -> None:
    """A blank question is an invalid-params error, not a crash."""
    messages = call(Daemon(repo_root=repo), "query", {"question": "  "})

    assert messages[0]["error"]["code"] == INVALID_PARAMS


def test_explain_streams_into_done_payload(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """explain streams tokens and finishes with file/line context."""
    stub = install_stub(monkeypatch)
    messages = call(
        Daemon(repo_root=repo),
        "explain",
        {"file": "app/payments.py", "start": 4, "end": 10},
    )

    streams = [message for message in messages if "stream" in message]
    done = messages[-1]
    assert streams
    assert done["done"] is True
    assert done["mode"] == "explain"
    assert done["file"].endswith("app/payments.py")
    assert (done["line_start"], done["line_end"]) == (4, 10)
    call_info = stub.calls[0]
    assert call_info["method"] == "explain"
    assert "def validate_payment" in str(call_info["code"])


def test_simplify_streams_with_same_shape(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """simplify mirrors explain (read-only preview)."""
    install_stub(monkeypatch)
    messages = call(
        Daemon(repo_root=repo),
        "simplify",
        {"file": "app/payments.py", "start": 4, "end": 10},
    )

    done = messages[-1]
    assert done["done"] is True
    assert done["mode"] == "simplify"


def test_explain_missing_file_is_reportable(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing file yields a -32603 error mentioning the path."""
    install_stub(monkeypatch)
    messages = call(Daemon(repo_root=repo), "explain", {"file": "nope.py"})

    assert messages[0]["error"]["code"] == INTERNAL_ERROR
    assert "File not found" in messages[0]["error"]["message"]


def test_explain_invalid_range_is_invalid_params(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-of-bounds range maps to -32602."""
    install_stub(monkeypatch)
    messages = call(Daemon(repo_root=repo), "explain", {"file": "app/payments.py", "start": 999})

    assert messages[0]["error"]["code"] == INVALID_PARAMS
    assert "Invalid range" in messages[0]["error"]["message"]


# ---------------------------------------------------------------------------
# index / status / analyze
# ---------------------------------------------------------------------------


def test_index_returns_stats_without_polluting_stdout(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """index answers with stats while its own output never hits stdout."""
    messages = call(Daemon(repo_root=repo), "index", {"repo": repo, "force": True})

    result = messages[0]["result"]
    assert result["ok"] is True
    assert result["repo_root"] == repo
    assert result["files"] == len(SAMPLE_FILES)
    assert result["symbols"] >= 7
    assert result["chunks"] >= 1

    captured = capsys.readouterr()
    assert captured.out == ""  # JSON-RPC owns stdout


def test_index_missing_directory_is_reportable(repo: str) -> None:
    """Indexing a path that is not a directory fails cleanly."""
    messages = call(Daemon(repo_root=repo), "index", {"repo": f"{repo}/missing-dir"})

    assert messages[0]["error"]["code"] == INTERNAL_ERROR
    assert "Not a directory" in messages[0]["error"]["message"]


def test_status_reports_index_state(repo: str) -> None:
    """status powers the status bar: counts, provider, version."""
    result = call(Daemon(repo_root=repo), "status")[0]["result"]

    assert result["indexed"] is True
    assert result["repo_root"] == repo
    assert result["files"] == len(SAMPLE_FILES)
    assert result["symbols"] >= 7
    assert result["chunks"] >= 1
    assert result["llm_provider"] == "ollama"
    assert result["version"] == __version__


def test_status_without_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh HOME reports indexed=false with zero counts."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    result = call(Daemon(repo_root=str(tmp_path / "repo")), "status")[0]["result"]

    assert result["indexed"] is False
    assert result["files"] == 0
    assert result["symbols"] == 0


def test_analyze_hotspots(repo: str) -> None:
    """Hotspots surfaces complex functions with file/line context."""
    result = call(Daemon(repo_root=repo), "analyze_hotspots", {"threshold": 1})[0]["result"]

    assert result["threshold"] == 1
    assert result["count"] >= 1
    hotspot = result["hotspots"][0]
    assert hotspot["file"] == "app/payments.py"
    assert hotspot["complexity"] >= 2


def test_analyze_coupling(repo: str) -> None:
    """Coupling lists modules with import totals."""
    result = call(Daemon(repo_root=repo), "analyze_coupling", {"threshold": 0})[0]["result"]

    assert result["count"] >= 1
    assert {"file", "imports", "imported_by", "total"} <= set(result["modules"][0])


def test_analyze_circular_detects_the_cycle(repo: str) -> None:
    """circ_a <-> circ_b is reported as a single two-node cycle."""
    result = call(Daemon(repo_root=repo), "analyze_circular")[0]["result"]

    assert result["circular"] is True
    assert result["count"] == 1
    assert {edge["from"] for edge in result["cycle"]} == {"circ_a.py", "circ_b.py"}


# ---------------------------------------------------------------------------
# Protocol: parse errors, shutdown, the stdio run loop
# ---------------------------------------------------------------------------


def test_shutdown_stops_the_daemon(repo: str) -> None:
    """shutdown acks first, then flips the running flag."""
    daemon = Daemon(repo_root=repo)
    messages = call(daemon, "shutdown")

    assert messages == [{"id": 1, "result": {"ok": True}}]
    assert daemon.running is False


def test_process_line_rejects_invalid_json() -> None:
    """Malformed lines answer -32700 with a null id."""
    daemon = Daemon(repo_root=None)
    messages: list[dict[str, Any]] = []
    process_line("{not json", daemon, messages.append)

    assert messages[0]["error"]["code"] == PARSE_ERROR
    assert messages[0]["id"] is None

    process_line("   \n", daemon, messages.append)
    assert len(messages) == 1  # blank lines are ignored


def test_run_daemon_loop_over_pipes(repo: str) -> None:
    """The full loop answers requests in order and exits on shutdown."""
    stdin = StringIO(
        json.dumps({"id": 1, "method": "status"})
        + "\n\n"
        + json.dumps({"id": 2, "method": "shutdown"})
        + "\n"
    )
    stdout = StringIO()

    code = run_daemon(repo_root=repo, stdin=stdin, stdout=stdout)

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert code == 0
    assert len(lines) == 2  # blank line produced no response
    assert lines[0]["id"] == 1 and lines[0]["result"]["indexed"] is True
    assert lines[1] == {"id": 2, "result": {"ok": True}}


def test_detect_repo_root_precedence(
    repo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--repo wins; otherwise a git cwd wins; else the latest index."""
    assert detect_repo_root(str(tmp_path)) == str(tmp_path)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert detect_repo_root(None) == repo  # no .git here -> latest index

    (elsewhere / ".git").mkdir()
    assert detect_repo_root(None) == str(elsewhere)


@pytest.mark.integration
def test_daemon_subprocess_roundtrip(repo: str, tmp_path: Path) -> None:
    """End-to-end: the real CLI daemon over real OS pipes."""
    requests = (
        json.dumps({"id": 1, "method": "find_symbol", "params": {"name": "validate_payment"}})
        + "\n"
        + json.dumps({"id": 2, "method": "status"})
        + "\n"
        + json.dumps({"id": 3, "method": "shutdown"})
        + "\n"
    )
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "codecompass.cli.main", "daemon", "--repo", repo],
        input=requests,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(tmp_path),
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [line["id"] for line in lines] == [1, 2, 3]
    assert lines[0]["result"]["found"] is True
    assert lines[1]["result"]["indexed"] is True
    assert lines[2] == {"id": 3, "result": {"ok": True}}
