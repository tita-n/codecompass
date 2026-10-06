# CodeCompass MCP Server

CodeCompass exposes your codebase's **structure** to AI agents through the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP) — Anthropic's
open standard for connecting assistants to tools. Instead of grepping files and
guessing, an agent asks CodeCompass for exact symbols, callers, imports, graphs
and blast radius, in milliseconds, with zero hallucinated paths.

## Why CodeCompass + MCP?

| Without CodeCompass | With CodeCompass (MCP) |
|---|---|
| Grep + read files into context | One `find_symbol` call returns file, lines, signature |
| Hallucinated file paths in large repos | Paths come from the real index — never invented |
| Context fills up, earlier files forgotten | Agents call tools instead of reading files |
| Guessing who calls what | `find_callers` resolves cross-file callers, tests included |
| "Is this change risky?" | `impact_analysis` returns risk + affected files + tests |
| ~30 s of grepping per question | ~7 ms per tool call |

The server is **read-only** and **fully local**: it reads the SQLite/ChromaDB
index built by `codecompass index` and never calls an LLM, the network, or any
cloud API.

## Setup

### 1. Index your repository

```bash
codecompass index /path/to/your/repo
```

The server answers for the **most recently indexed** repository. Re-run
`codecompass index` after code changes so agents see fresh data.

### 2. Connect your agent

**Claude Code** — add to your MCP config
(`~/.claude.json`, `.mcp.json`, or via `claude mcp add`):

```json
{
  "mcpServers": {
    "codecompass": {
      "command": "codecompass",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

**Cursor** — Settings → MCP → Add Server:

```json
{
  "mcpServers": {
    "codecompass": {
      "command": "codecompass",
      "args": ["mcp"]
    }
  }
}
```

**OpenCode** (or any MCP client) — same JSON config; OpenCode also accepts a
`command`/`args` pair in its MCP config file.

**HTTP transport** (for clients that prefer URLs, e.g. remote or shared setups):

```bash
codecompass mcp --transport http --port 8888     # binds to 127.0.0.1 only
```

The streamable-HTTP endpoint is `http://127.0.0.1:8888/mcp`. Binding to anything
other than loopback prints a warning — there is no authentication, so keep it
local unless you front it with a proxy.

## Tool reference

All tools return JSON text with stable keys and a `count`-style field so agents
can short-circuit ("count == 0 → skip deeper analysis").

### `find_symbol(name, kind?)`

Locate symbols by name, optionally filtered by kind (`function`, `class`,
`method`).

```json
{
  "found": true,
  "name": "validate_payment",
  "count": 1,
  "symbols": [
    {
      "name": "validate_payment",
      "kind": "function",
      "file": "app/payments.py",
      "line_start": 4,
      "line_end": 10,
      "signature": "(amount: float, currency: str)",
      "docstring": "Validate a payment amount and currency before charging.",
      "complexity": 3
    }
  ]
}
```

Not found → `{"found": false, "count": 0, "symbols": []}` (never an error).

### `find_callers(symbol)`

Every symbol that calls `symbol`, across files, including tests. Each entry has
the caller's file/line range and the `call_line` of the call site.

```json
{
  "symbol": "validate_payment",
  "callers": [
    {"name": "checkout", "file": "app/cart.py", "line_start": 6, "line_end": 10, "call_line": 10},
    {"name": "test_validate_payment_rejects_negative", "file": "tests/test_payments.py",
     "line_start": 6, "line_end": 8, "call_line": 8}
  ],
  "count": 2
}
```

### `find_callees(symbol)`

What a symbol calls. `line_start`/`line_end` describe the callee's definition;
`call_line` is the call site.

```json
{
  "symbol": "checkout",
  "callees": [
    {"name": "validate_payment", "file": "app/payments.py",
     "line_start": 4, "line_end": 10, "call_line": 10}
  ],
  "count": 1
}
```

### `find_imports(module)`

Who imports a module, and what that module imports. Accepts a dotted module
(`app.payments`) or a repo-relative path.

```json
{
  "module": "app.payments",
  "file": "app/payments.py",
  "imported_by": [
    {"file": "app/cart.py", "line": 3, "names": ["validate_payment"]},
    {"file": "tests/test_payments.py", "line": 3, "names": ["process_payment", "validate_payment"]}
  ],
  "imported_by_count": 2,
  "imports": [],
  "imports_count": 0
}
```

### `get_dependency_graph(symbol, depth = 2)`

A callee subgraph as flat node/edge lists (cycle-safe; `depth` is clamped to 10).
Duplicate names are disambiguated as `name@file:line`.

```json
{
  "root": "main",
  "found": true,
  "depth": 2,
  "nodes": [
    {"id": "main", "name": "main", "kind": "function", "file": "app/main.py",
     "line_start": 6, "line_end": 10, "signature": "()"},
    {"id": "login@app/auth.py:11", "name": "login", "kind": "function",
     "file": "app/auth.py", "line_start": 11, "line_end": 11, "signature": "(email: str, password: str)"}
  ],
  "edges": [
    {"from": "main", "to": "login@app/auth.py:11", "line": 8}
  ],
  "node_count": 2,
  "edge_count": 1
}
```

### `impact_analysis(symbol, max_depth = 3)`

Blast radius of changing a symbol. Risk uses the same heuristic as the CLI
(`HIGH` >5 direct callers, `MEDIUM` 2–5, `LOW` 0–1), so agents and humans always
agree.

```json
{
  "symbol": "validate_payment",
  "found": true,
  "risk": "MEDIUM",
  "direct_callers": 2,
  "total_affected": 2,
  "affected_files": ["app/cart.py", "app/payments.py", "tests/test_payments.py"],
  "affected_tests": ["tests/test_payments.py"],
  "reasoning": "2 direct caller(s), 2 total affected symbol(s) across 3 file(s) (max_depth=3). 1 test file(s) exercise this symbol."
}
```

## Errors

- Unknown tool → a protocol error (`ToolError: Unknown tool ...`), so the client
  reports it without the server dying.
- Invalid arguments → a protocol validation error from the SDK.
- Tool failures (empty name, no index, …) → normal JSON text:
  `{"error": "ToolError: ...", "tool": "find_symbol"}`.
- No index at all → `{"error": "ToolError: No indexed repository found. Run 'codecompass index <path>' first.", ...}`

## Logging

Every tool call is logged to `~/.codecompass/logs/mcp.log` (rotating, 10 MB × 3
backups) with the tool name, its arguments and the duration in milliseconds.
File contents are never logged.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Server does not start | Run `codecompass mcp --list-tools`; if that works, the config/command is wrong. |
| `"No indexed repository found"` | `codecompass index /path/to/repo` first. |
| Symbols not found | Stale index — re-run `codecompass index` (incremental, so it is fast). |
| Agent ignores the tools | Confirm the client shows a `codecompass` MCP server with 6 tools after connecting. |
| `codecompass: command not found` | Use the absolute path to the executable, or activate the virtualenv the agent inherits. |
| Wrong repository answered | The server uses the most recently indexed repo; re-index the one you want. |

## Verify it works

```bash
codecompass mcp --list-tools     # prints the 6 tool names without starting a server
```

See `scripts/mcp_smoke.md` for a copy-paste manual test with Claude Code.