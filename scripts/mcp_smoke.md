# MCP Smoke Test

A 5-minute manual check that CodeCompass works as an MCP server with a real
agent. See [`docs/mcp.md`](../docs/mcp.md) for the full reference.

## 1. Prepare a repository

```bash
mkdir -p /tmp/mcp-demo/app && cd /tmp/mcp-demo
cat > app/payments.py <<'PY'
"""Payments module."""


def validate_payment(amount: float, currency: str) -> bool:
    """Validate a payment amount and currency before charging."""
    if amount <= 0:
        return False
    if currency not in ("USD", "EUR", "GBP"):
        return False
    return amount < 10000
PY
cat > app/cart.py <<'PY'
"""Shopping cart."""

from app.payments import validate_payment


def checkout(items: list, amount: float) -> bool:
    """Check out a cart after validating the payment."""
    return bool(items) and validate_payment(amount, "USD")
PY
```

Index it (the server answers for the most recently indexed repository):

```bash
codecompass index /tmp/mcp-demo
```

## 2. Sanity-check the tools (no agent needed)

```bash
codecompass mcp --list-tools
```

Expected: a table with `find_symbol`, `find_callers`, `find_callees`,
`find_imports`, `get_dependency_graph`, `impact_analysis`.

## 3. Connect Claude Code

```bash
claude mcp add codecompass -- codecompass mcp --transport stdio
# or add to ~/.claude.json:
#   {"mcpServers": {"codecompass": {"command": "codecompass",
#                                    "args": ["mcp", "--transport", "stdio"]}}}
claude   # the server should appear in /mcp with 6 tools
```

## 4. Try these prompts

1. *"Where is `validate_payment` defined? Use the CodeCompass tools."*
2. *"Find all callers of `validate_payment`."*
3. *"What happens if I change `validate_payment`? Show the impact and the tests."*
4. *"Show the dependency graph of `checkout` with depth 2."*
5. *"Which modules import `app.payments`?"*

## 5. Expected output

Prompt 1 — the agent should report exact locations, not guess:

```text
validate_payment is defined in app/payments.py, lines 4-10:

    def validate_payment(amount: float, currency: str) -> bool:
        """Validate a payment amount and currency before charging."""
        ...
```

Prompt 3 — the agent should surface the risk and the covering test:

```text
Risk: MEDIUM — 2 direct callers, 2 affected symbols across 3 files.
Affects: app/cart.py, app/payments.py, tests/test_payments.py
Covered by: tests/test_payments.py
```

Prompt 5 — the importer list comes straight from the index:

```text
app/cart.py:3 imports validate_payment
tests/test_payments.py:3 imports process_payment, validate_payment
```

## 6. Troubleshooting during the smoke test

| Problem | Fix |
|---|---|
| No `codecompass` server in the client | Check `codecompass mcp --list-tools` works in that shell |
| `"No indexed repository found"` | `codecompass index /tmp/mcp-demo` |
| Agent answers without using tools | Ask explicitly *"use the CodeCompass MCP tools"* |
| Nothing logged | See `~/.codecompass/logs/mcp.log` (tool name, args, duration) |

## Optional: HTTP transport

```bash
codecompass mcp --transport http --port 8888
curl -s -X POST http://127.0.0.1:8888/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

Expect an SSE `event: message` line containing the server info.