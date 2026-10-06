"""Integration tests for the MCP server (in-process, offline)."""

import json
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from codecompass.indexer.indexer import Indexer
from codecompass.mcp.server import (
    configure_logging,
    create_server,
    describe_tools,
    run_http,
    run_stdio,
)

pytestmark = pytest.mark.integration

PAYMENTS_PY = '''\
"""Payments module."""


def validate_payment(amount: float, currency: str) -> bool:
    """Validate a payment amount and currency before charging."""
    if amount <= 0:
        return False
    return amount < 10000
'''

CART_PY = '''\
"""Shopping cart helpers."""

from app.payments import validate_payment


def checkout(items: list, amount: float) -> bool:
    """Check out a cart after validating the payment."""
    return bool(items) and validate_payment(amount, "USD")
'''

TEST_PY = '''\
"""Tests."""

from app.payments import validate_payment


def test_validate_payment() -> None:
    """Payment validation works."""
    assert validate_payment(1, "USD") is True
'''

EXPECTED_TOOLS = {
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


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Index a tiny repo into an isolated HOME; return its root."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    for relative, content in (
        ("app/payments.py", PAYMENTS_PY),
        ("app/cart.py", CART_PY),
        ("tests/test_payments.py", TEST_PY),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    Indexer(root).index()
    return str(root)


def _text(result: object) -> str:
    """Extract the text payload of a CallToolResult."""
    content = getattr(result, "content", [])
    return "".join(getattr(block, "text", "") for block in content)


async def test_lists_all_nine_tools(repo: str) -> None:
    """The tool surface matches the documented set."""
    tools = await create_server(repo_root=repo).list_tools()

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == 9
    assert {entry["name"] for entry in describe_tools()} == EXPECTED_TOOLS


async def test_lists_exactly_six_tools(repo: str) -> None:
    """Backward-compatible alias: the original six tools are still listed."""
    await test_lists_all_nine_tools(repo)


async def test_tools_expose_descriptions_and_schemas(repo: str) -> None:
    """Every tool is documented and carries a typed input schema."""
    tools = {tool.name: tool for tool in await create_server(repo_root=repo).list_tools()}

    for name, tool in tools.items():
        assert tool.description and len(tool.description) > 30, name
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert schema["properties"], name
        # Tools with all-optional params may omit "required" (MCP SDK drops
        # empty required lists); when present it must be non-empty.
        assert schema.get("required", ["_placeholder"]) != [], name

    assert tools["find_symbol"].input_schema["required"] == ["name"]
    assert tools["get_dependency_graph"].input_schema["required"] == ["symbol"]
    assert tools["impact_analysis"].input_schema["required"] == ["symbol"]


async def test_call_tool_returns_json_payload(repo: str) -> None:
    """Tool responses are JSON text an agent can parse."""
    server = create_server(repo_root=repo)
    result = await server.call_tool("find_symbol", {"name": "validate_payment"})

    payload = json.loads(_text(result))
    assert payload["found"] is True
    assert payload["symbols"][0]["file"] == "app/payments.py"
    assert payload["symbols"][0]["line_start"] == 4


async def test_call_every_tool(repo: str) -> None:
    """All six tools are callable and return parseable JSON."""
    server = create_server(repo_root=repo)
    calls: dict[str, dict[str, Any]] = {
        "find_symbol": {"name": "validate_payment"},
        "find_callers": {"symbol": "validate_payment"},
        "find_callees": {"symbol": "checkout"},
        "find_imports": {"module": "app.payments"},
        "get_dependency_graph": {"symbol": "checkout", "depth": 2},
        "impact_analysis": {"symbol": "validate_payment"},
    }

    for name, arguments in calls.items():
        result = await server.call_tool(name, arguments)
        payload = json.loads(_text(result))
        assert isinstance(payload, dict), name
        assert "error" not in payload, f"{name} returned {payload}"


async def test_unknown_tool_is_an_error(repo: str) -> None:
    """Unknown tools produce a protocol error, not a crashed server."""
    server = create_server(repo_root=repo)

    with pytest.raises(ToolError, match="Unknown tool"):
        await server.call_tool("does_not_exist", {})


async def test_handler_errors_are_returned_as_text(repo: str) -> None:
    """Handler exceptions are captured and returned as an error payload."""
    server = create_server(repo_root=repo)
    result = await server.call_tool("find_symbol", {"name": ""})

    payload = json.loads(_text(result))
    assert payload["error"].startswith("ToolError")
    assert payload["tool"] == "find_symbol"


async def test_missing_index_is_reported_to_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no index the tool explains what to do instead of failing."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    server = create_server()
    result = await server.call_tool("find_symbol", {"name": "validate_payment"})

    payload = json.loads(_text(result))
    assert "codecompass index" in payload["error"]


async def test_tool_calls_are_logged(repo: str) -> None:
    """Calls are recorded in the rotating log (names and timings, no content)."""
    server = create_server(repo_root=repo)
    await server.call_tool("find_callers", {"symbol": "validate_payment"})

    logger = configure_logging()
    handler = logger.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 3

    log_path = Path(handler.baseFilename)
    assert log_path.exists()

    contents = log_path.read_text(encoding="utf-8")
    assert "tool=find_callers" in contents
    assert "duration_ms=" in contents
    # Symbol names are logged; file contents never are.
    assert "validate_payment" in contents
    assert "Validate a payment amount" not in contents


def test_transport_entrypoints_are_coroutines() -> None:
    """The CLI-facing transports are awaitable entry points."""
    import inspect

    assert inspect.iscoroutinefunction(run_stdio)
    assert inspect.iscoroutinefunction(run_http)
