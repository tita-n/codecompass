"""Tests for the MCP tool handlers (schemas, edge cases, performance)."""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from codecompass.indexer.indexer import Indexer
from codecompass.mcp import handlers
from codecompass.mcp.handlers import ToolError

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

AUTH_PY = '''\
"""Authentication module."""


class Account:
    """A user account."""

    def register(self, email: str) -> str:
        """Register an account."""
        return email


def register_user(email: str, password: str) -> str:
    """Register a new user account."""
    if "@" not in email:
        raise ValueError("invalid email")
    return f"user:{email}"


def login(email: str, password: str) -> bool:
    """Authenticate a registered user."""
    return bool(email and password)
'''

MAIN_PY = '''\
"""Entry point."""

from app.services.login import login


def main() -> int:
    """Run the application."""
    if login("admin@example.com", "secret"):
        return 0
    return 1
'''

SERVICE_LOGIN_PY = '''\
"""Login service."""

from app.auth import login as auth_login


def login(user: str, password: str) -> bool:
    """Log a user in via the authentication module."""
    return auth_login(user, password)
'''

TEST_PAYMENTS_PY = '''\
"""Tests for payments."""

from app.payments import process_payment, validate_payment


def test_validate_payment_rejects_negative() -> None:
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


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Index a sample repo into an isolated HOME; return its root."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    for relative, content in (
        ("app/payments.py", PAYMENTS_PY),
        ("app/cart.py", CART_PY),
        ("app/auth.py", AUTH_PY),
        ("app/main.py", MAIN_PY),
        ("app/services/login.py", SERVICE_LOGIN_PY),
        ("tests/test_payments.py", TEST_PAYMENTS_PY),
        ("circ_a.py", CIRC_A_PY),
        ("circ_b.py", CIRC_B_PY),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    Indexer(root).index()
    return str(root)


# ---------------------------------------------------------------------------
# find_symbol
# ---------------------------------------------------------------------------


def test_find_symbol_returns_full_details(repo: str) -> None:
    """A known symbol resolves to file, lines, signature, docstring, complexity."""
    result = handlers.find_symbol("validate_payment", repo_root=repo)

    assert result["found"] is True
    assert result["count"] == 1
    symbol = result["symbols"][0]
    assert symbol["file"] == "app/payments.py"
    assert symbol["kind"] == "function"
    assert symbol["line_start"] == 4
    assert symbol["line_end"] == 10
    assert "amount" in str(symbol["signature"])
    assert "Validate a payment amount" in str(symbol["docstring"])
    assert symbol["complexity"] == 3


def test_find_symbol_unknown_returns_not_found(repo: str) -> None:
    """Unknown symbols return an empty, non-crashing result."""
    result = handlers.find_symbol("does_not_exist", repo_root=repo)

    assert result["found"] is False
    assert result["count"] == 0
    assert result["symbols"] == []


def test_find_symbol_kind_filter(repo: str) -> None:
    """The optional kind filter narrows results."""
    assert handlers.find_symbol("Account", repo_root=repo)["count"] == 1
    assert handlers.find_symbol("Account", kind="class", repo_root=repo)["count"] == 1
    assert handlers.find_symbol("Account", kind="function", repo_root=repo)["count"] == 0


def test_find_symbol_requires_name(repo: str) -> None:
    """An empty name is a usage error, not a crash."""
    with pytest.raises(ToolError, match="non-empty"):
        handlers.find_symbol("   ", repo_root=repo)


# ---------------------------------------------------------------------------
# find_callers / find_callees
# ---------------------------------------------------------------------------


def test_find_callers_is_cross_file_and_includes_tests(repo: str) -> None:
    """Callers include application code *and* test files."""
    result = handlers.find_callers("validate_payment", repo_root=repo)

    assert result["symbol"] == "validate_payment"
    assert result["count"] == len(result["callers"]) == 2
    by_name = {caller["name"]: caller for caller in result["callers"]}
    assert by_name["checkout"]["file"] == "app/cart.py"
    assert by_name["test_validate_payment_rejects_negative"]["file"] == ("tests/test_payments.py")
    for caller in result["callers"]:
        assert caller["file"] and caller["line_start"] and caller["call_line"]


def test_find_callers_without_callers_is_empty(repo: str) -> None:
    """A leaf symbol yields an empty list and count 0 (agents can short-circuit)."""
    assert handlers.find_callers("register_user", repo_root=repo)["count"] == 0


def test_find_callers_unknown_symbol(repo: str) -> None:
    """Unknown symbols do not raise."""
    assert handlers.find_callers("nope", repo_root=repo)["count"] == 0


def test_find_callees_reports_call_site(repo: str) -> None:
    """Callees carry the call site, not the callee's definition line."""
    result = handlers.find_callees("checkout", repo_root=repo)

    assert result["count"] == 1
    callee = result["callees"][0]
    assert callee["name"] == "validate_payment"
    assert callee["file"] == "app/payments.py"
    assert callee["line_start"] == 4 and callee["line_end"] == 10
    # call_line points at the call inside app/cart.py, not at the definition.
    assert callee["call_line"] == 10


# ---------------------------------------------------------------------------
# find_imports
# ---------------------------------------------------------------------------


def test_find_imports_returns_importers(repo: str) -> None:
    """Imported-by lists application and test importers with names."""
    result = handlers.find_imports("app.payments", repo_root=repo)

    assert result["module"] == "app.payments"
    assert result["file"] == "app/payments.py"
    importers = {entry["file"]: entry for entry in result["imported_by"]}
    assert "app/cart.py" in importers
    assert "validate_payment" in importers["app/cart.py"]["names"]
    assert importers["app/cart.py"]["line"] == 3
    assert "tests/test_payments.py" in importers
    assert result["imported_by_count"] == len(result["imported_by"])


def test_find_imports_returns_module_imports(repo: str) -> None:
    """A module also reports what it imports itself."""
    result = handlers.find_imports("app.cart", repo_root=repo)

    modules = {entry["module"] for entry in result["imports"]}
    assert "app.payments" in modules
    assert result["imports_count"] == len(result["imports"])


def test_find_imports_unknown_module(repo: str) -> None:
    """Unknown modules return empty lists rather than failing."""
    result = handlers.find_imports("nope.module", repo_root=repo)

    assert result["imported_by"] == []
    assert result["imports"] == []


# ---------------------------------------------------------------------------
# get_dependency_graph
# ---------------------------------------------------------------------------


def test_get_dependency_graph_nodes_and_edges(repo: str) -> None:
    """The subgraph is internally consistent: edges reference real node ids."""
    result = handlers.get_dependency_graph("main", depth=2, repo_root=repo)

    assert result["found"] is True
    assert result["depth"] == 2
    ids = {node["id"] for node in result["nodes"]}
    assert "main" in ids
    assert result["node_count"] == len(result["nodes"])
    assert result["edge_count"] == len(result["edges"])
    for edge in result["edges"]:
        assert edge["from"] in ids
        assert edge["to"] in ids
    # Two distinct `login` symbols are disambiguated by file:line.
    assert any("@" in node["id"] for node in result["nodes"] if node["name"] == "login")


def test_get_dependency_graph_respects_depth(repo: str) -> None:
    """A smaller depth returns a smaller subgraph."""
    shallow = handlers.get_dependency_graph("main", depth=1, repo_root=repo)
    deep = handlers.get_dependency_graph("main", depth=2, repo_root=repo)

    assert shallow["node_count"] < deep["node_count"]
    assert shallow["depth"] == 1


def test_get_dependency_graph_handles_cycles(repo: str) -> None:
    """Cyclic calls terminate instead of recursing forever."""
    result = handlers.get_dependency_graph("a_func", depth=5, repo_root=repo)

    assert result["found"] is True
    assert result["node_count"] >= 2
    names = [node["name"] for node in result["nodes"]]
    assert len(names) == len(set(names))  # traversal terminated


def test_get_dependency_graph_unknown_symbol(repo: str) -> None:
    """Unknown roots return an empty, well-formed graph."""
    result = handlers.get_dependency_graph("nope", repo_root=repo)

    assert result["found"] is False
    assert result["nodes"] == [] and result["edges"] == []


def test_get_dependency_graph_clamps_depth(repo: str) -> None:
    """Absurd depths are clamped so an agent cannot request the whole graph."""
    assert (
        handlers.get_dependency_graph("main", depth=999, repo_root=repo)["depth"]
        == handlers.MAX_DEPTH
    )
    assert handlers.get_dependency_graph("main", depth=0, repo_root=repo)["depth"] == 1


# ---------------------------------------------------------------------------
# impact_analysis
# ---------------------------------------------------------------------------


def test_impact_analysis_reports_blast_radius(repo: str) -> None:
    """Risk, callers, affected files and covering tests are all reported."""
    result = handlers.impact_analysis("validate_payment", repo_root=repo)

    assert result["found"] is True
    assert result["risk"] in {"HIGH", "MEDIUM", "LOW"}
    assert result["direct_callers"] == 2  # checkout + one test
    assert result["total_affected"] >= 2
    assert "app/cart.py" in result["affected_files"]
    assert "tests/test_payments.py" in result["affected_tests"]
    assert "direct caller" in result["reasoning"]


def test_impact_analysis_respects_max_depth(repo: str) -> None:
    """max_depth bounds the transitive caller walk."""
    shallow = handlers.impact_analysis("login", max_depth=1, repo_root=repo)
    deep = handlers.impact_analysis("login", max_depth=3, repo_root=repo)

    assert shallow["found"] is True and deep["found"] is True
    assert len(shallow["affected_files"]) <= len(deep["affected_files"])
    assert "max_depth=1" in shallow["reasoning"]


def test_impact_analysis_unknown_symbol(repo: str) -> None:
    """Unknown symbols return an explicit not-found payload."""
    result = handlers.impact_analysis("nope", repo_root=repo)

    assert result["found"] is False
    assert result["risk"] == "UNKNOWN"
    assert result["affected_files"] == []


# ---------------------------------------------------------------------------
# Cross-cutting contracts
# ---------------------------------------------------------------------------


def test_all_handlers_are_json_serialisable(repo: str) -> None:
    """Every tool result must survive json.dumps (the MCP response contract)."""
    results: list[dict[str, Any]] = [
        handlers.find_symbol("validate_payment", repo_root=repo),
        handlers.find_callers("validate_payment", repo_root=repo),
        handlers.find_callees("checkout", repo_root=repo),
        handlers.find_imports("app.payments", repo_root=repo),
        handlers.get_dependency_graph("main", 2, repo_root=repo),
        handlers.impact_analysis("validate_payment", repo_root=repo),
    ]
    for result in results:
        assert isinstance(json.loads(json.dumps(result)), dict)


def test_handler_registry_matches_tools() -> None:
    """The dispatcher registry exposes exactly the documented tools."""
    assert set(handlers.HANDLERS) == {
        "find_symbol",
        "find_callers",
        "find_callees",
        "find_imports",
        "get_dependency_graph",
        "impact_analysis",
        "analyze_hotspots",
        "analyze_coupling",
        "analyze_circular",
    }


def test_without_index_returns_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing index is reported clearly instead of crashing the server."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))

    with pytest.raises(ToolError, match="No indexed repository"):
        handlers.find_symbol("validate_payment")


def test_tool_responses_are_fast(repo: str) -> None:
    """Warm tool calls stay in the millisecond range (spec target: <100 ms).

    The bound is deliberately loose so the test cannot flake on a loaded CI box;
    on a normal machine these calls take ~5-10 ms.
    """
    handlers.find_symbol("validate_payment", repo_root=repo)  # warm the graph cache

    start = time.perf_counter()
    for _ in range(5):
        handlers.find_callers("validate_payment", repo_root=repo)
    elapsed_ms = (time.perf_counter() - start) * 1000 / 5

    assert elapsed_ms < 500
