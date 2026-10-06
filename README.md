# CodeCompass

> AI-powered codebase intelligence layer.

[![CI](https://github.com/codecompass/codecompass/actions/workflows/ci.yml/badge.svg)](https://github.com/codecompass/codecompass/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/codecompass/codecompass?label=install%3A%20one%20command)](https://github.com/codecompass/codecompass/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## What is CodeCompass?

CodeCompass is a lightweight, offline-first code intelligence layer that helps you navigate, understand, and analyze codebases instantly. It builds a persistent semantic graph of your repository and exposes it via CLI, VS Code extension, and MCP (Model Context Protocol) for AI agents.

**No cloud. No telemetry. No hallucinations.**

## Features

- 🔍 **Instant answers** — Ask natural-language questions about your codebase
- 🌐 **Dependency visualization** — See call graphs and impact analysis
- 🧠 **AI agent integration** — Make Claude Code, Cursor, and OpenCode 10x smarter via MCP
- 🚀 **Lightning fast** — < 2-second responses on 8GB RAM
- 🔒 **Privacy-first** — 100% offline. Your code never leaves your machine.
- 💻 **Cross-platform** — Windows, macOS (Intel + Apple Silicon), Linux
- 🆓 **Free forever** — Open source core

## Quick Start

### Installation

```bash
curl -sSL https://codecompass.dev/install | sh
```

See [`docs/installation.md`](docs/installation.md) for Homebrew, Winget,
Debian, and manual installs.

Or via pip:

```bash
pip install codecompass
```

### Index your repository

```bash
codecompass index /path/to/your/repo
```

### Ask a question

```bash
codecompass query "where is the payment validation logic?"
```

### Find callers (across files)

```bash
codecompass callers validate_token
```

### Trace a symbol's dependencies

```bash
codecompass trace main --depth 3
```

### Analyze the codebase

```bash
codecompass analyze hotspots --threshold 10
codecompass analyze coupling --threshold 5
codecompass analyze circular
```

### Start MCP server for AI agents

```bash
codecompass mcp --port 8888
```

## Query Engine

After indexing, CodeCompass builds an in-memory call graph (NetworkX) from the
SQLite index and answers questions **without any LLM** — pure graph traversal.
Call sites are resolved across files using the import graph (aliases, relative
imports, and wildcards included).

```bash
# Who calls this symbol? (resolved across files, including tests)
codecompass callers validate_token

# What does this symbol call?
codecompass callees login

# Dependency tree (callees) up to N levels
codecompass trace main --depth 3

# Shortest call path between two symbols
codecompass path main validate_token

# Blast radius: callers, affected tests, and a risk score (HIGH/MEDIUM/LOW)
codecompass impact validate_token

# Complexity hotspots (cyclomatic complexity is computed during indexing)
codecompass analyze hotspots --threshold 10

# Tightly coupled modules
codecompass analyze coupling --threshold 5

# Circular dependencies in the module graph
codecompass analyze circular
```

Sample output:

```text
$ codecompass trace main --depth 3
Dependencies
└── main (app/main.py:6)
    └── login (app/services/login.py:10)
        ├── validate_token (app/auth.py:4)
        └── get_user (app/services/user.py:4)

$ codecompass impact validate_token
╭──────────────╮
│ Risk: MEDIUM │
╰─ 4 symbols t─╯
Direct callers (3) of validate_token ...
Affected test files:
  tests/test_auth.py
```

## Semantic Search

Indexing also chunks every file (500 characters with 200-character overlap,
attributed to the enclosing function/class), embeds each chunk with the local
`all-MiniLM-L6-v2` SentenceTransformer model (384-dim, ~80 MB, CPU-friendly)
and stores the vectors in ChromaDB alongside an SQLite FTS5 index.

`codecompass query` then runs **hybrid search**: vector similarity (Chroma,
cosine) + keyword matching (SQLite FTS5/BM25), merged with a weighted score so
chunks found by both signals are boosted. Everything runs offline on your
machine — no code ever leaves your machine.

```bash
# Natural-language question -> top-N chunks with file/line/symbol citations
codecompass query "where is the payment validation logic?"

# Restrict to a file, change result count, or show full chunks + score breakdown
codecompass query "how does authentication work?" --file app/auth.py --limit 3 --verbose
```

Sample output:

```text
$ codecompass query "Where is the payment validation logic?"
                          Top 5 results
┌──────────────────┬───────┬──────────────────┬───────┬─────────────────────────┐
│ File             │ Lines │ Symbol           │ Score │ Preview                 │
├──────────────────┼───────┼──────────────────┼───────┼─────────────────────────┤
│ app/payments.py  │ 4-10  │ validate_payment │  1.00 │ def validate_payment... │
│ app/payments.py  │ 1-3   │ -                │  0.79 │ """Payment processing...│
│ app/cart.py      │ 6-10  │ checkout         │  0.63 │ def checkout(items...   │
└──────────────────┴───────┴──────────────────┴───────┴─────────────────────────┘
```

Incremental indexing re-chunks and re-embeds only the files that changed;
deleting a file also removes its chunks from both SQLite and ChromaDB.

## LLM Setup

Once indexed, CodeCompass can turn retrieved chunks into a natural-language
answer with citations. The provider layer is pluggable and **local by default**.

### Local (default, fully private)

```bash
# 1. Install Ollama (https://ollama.com), then:
ollama serve
ollama pull codellama:7b

# 2. Ask questions - your code never leaves the machine
codecompass query "how does authentication work?"
```

The default provider is `ollama` (model `codellama:7b`) against
`http://localhost:11434`. Point it elsewhere with `llm.base_url` in
`~/.codecompass/config.yaml` or the `OLLAMA_HOST` environment variable.

### Cloud providers (opt-in)

Cloud providers are only used when you explicitly select them **and** store a
key. Keys are kept in your **OS keyring** - never in `config.yaml`:

```bash
codecompass config set-key anthropic     # prompts securely, stores in keyring
codecompass config set-key openai --key sk-...   # or pass non-interactively

# then select the provider in ~/.codecompass/config.yaml
#   llm:
#     provider: anthropic
#     model: claude-3-5-sonnet-20241022
```

Keys are read from the keyring first, then from `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / `GOOGLE_API_KEY`. `codecompass config` shows whether a key is
configured (never its value).

> **Privacy:** with a cloud provider your code is sent to that vendor.
> CodeCompass redacts apparent secrets (`sk-...`, `api_key=...`, private keys,
> AWS/GitHub tokens) before sending, but only sent code counts - treat cloud
> providers as an explicit opt-in. Set `llm.provider: none` for retrieval-only
> mode, or pass `--no-llm` to any query.

### Configuration

`~/.codecompass/config.yaml` (create with `codecompass init`):

```yaml
llm:
  provider: ollama          # none | ollama | anthropic | openai | google
  model: codellama:7b
  temperature: 0.3
  max_tokens: 2048
  stream: true
  timeout_seconds: 60
  base_url: null            # Ollama endpoint override
```

### Example output

```text
$ codecompass query "where is the payment validation logic?"
╭───────────────────────────────────────────────╮
│ Query: where is the payment validation logic? │
╰───────────────────────────────────────────────╯
Payment validation is implemented in **validate_payment** [app/payments.py:4-10].
It rejects non-positive amounts and unsupported currencies [app/payments.py:4-10],
and the cart calls it during checkout [app/cart.py:6-10].

                   Sources (3)
┏━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ # ┃ File            ┃ Lines ┃ Symbol           ┃
┡━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ 1 │ app/payments.py │ 4-10  │ validate_payment │
│ 2 │ app/payments.py │ 1-3   │ -                │
│ 3 │ app/cart.py     │ 6-10  │ checkout         │
└───┴─────────────────┴───────┴──────────────────┘
```

Citations are built from the retrieved chunks, so a file or line range can only
appear if retrieval actually found it. Answers stream token by token.

```bash
# Read-only code review commands
codecompass explain app/cart.py --start 6 --end 10
codecompass simplify app/cart.py --start 6 --end 10
```

```text
$ codecompass simplify app/cart.py --start 6 --end 10
╭──────────────────────────────────────╮
│ Simplify: app/cart.py                │
╰─────────── Lines: 6 → 10 ────────────╯
Read-only preview — no files are modified.
```python
def checkout(items: list, amount: float) -> bool:
    """Check out a cart after validating the payment."""
    return bool(items) and validate_payment(amount, "USD")
```
- Collapses the two guards into one boolean expression.
```

If the provider is unreachable, CodeCompass says so and falls back to the raw
retrieved chunks instead of failing:

```text
⚠ LLM answer unavailable: Could not reach Ollama at http://localhost:11434
  (ConnectError). Is it running? Start it with: ollama serve
Falling back to the retrieved chunks.
```

## Commands

| Command | Description |
|---------|-------------|
| `codecompass index /path` | Index a repository |
| `codecompass query "question"` | Ask a question: LLM answer with citations (`--limit`, `--file`, `--no-llm`, `--raw`, `--verbose`) |
| `codecompass trace --from symbol --depth N` | Show dependency tree |
| `codecompass callers symbol` | Show all callers of a symbol |
| `codecompass callees symbol` | Show all callees of a symbol |
| `codecompass path --from A --to B` | Show call path between symbols |
| `codecompass impact --on symbol` | Impact analysis with blast radius |
| `codecompass analyze hotspots --threshold 10` | Find complex functions |
| `codecompass analyze coupling --min 15` | Find tightly coupled modules |
| `codecompass analyze circular` | Detect circular dependencies |
| `codecompass explain file.py --start 10 --end 20` | Explain a code block with the LLM |
| `codecompass simplify file.py --start 10 --end 20` | Read-only simplification preview |
| `codecompass config` | Show configuration and LLM provider status |
| `codecompass config set-key <provider>` | Store a cloud API key in your OS keyring |
| `codecompass mcp` | Start the MCP server for AI agents (`--transport stdio\|http`, `--host`, `--port`, `--list-tools`) |

## Development

### Setup

```bash
git clone https://github.com/codecompass/codecompass
cd codecompass
./scripts/setup_dev.sh
```

### Run tests

```bash
pytest
```

### Build

```bash
pip install -e .
```

## AI Agent Integration (MCP)

CodeCompass speaks the [Model Context Protocol](https://modelcontextprotocol.io),
so Claude Code, Cursor, OpenCode and friends can query your codebase's structure
instead of grepping and guessing. Six tools — `find_symbol`, `find_callers`,
`find_callees`, `find_imports`, `get_dependency_graph`, `impact_analysis` — answer
in milliseconds and cannot hallucinate file paths.

```bash
codecompass index /path/to/repo        # build the index first
codecompass mcp                        # start the server (stdio)
codecompass mcp --list-tools           # show the exposed tools
```

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

The server is read-only and fully local: it never calls an LLM, the network, or
a cloud API. Full setup, tool reference and troubleshooting:
**[`docs/mcp.md`](docs/mcp.md)**.

## MCP Integration

CodeCompass exposes MCP tools that any AI agent can use:

- `find_symbol(name)` — Returns file + line + signature
- `find_callers(symbol)` — Returns all functions that call it
- `find_callees(symbol)` — Returns all functions it calls
- `find_imports(module)` — Returns all imports of a module
- `get_dependency_graph(symbol, depth)` — Returns JSON graph
- `impact_analysis(symbol)` — Returns blast radius + affected tests

### Claude Code Configuration

Add to your Claude Code config:

```json
{
  "mcpServers": {
    "codecompass": {
      "command": "codecompass",
      "args": ["mcp", "--port", "8888"]
    }
  }
}
```

## Roadmap

| Phase | Status |
|-------|--------|
| ✅ Project Scaffolding | Complete |
| ✅ Indexing Engine | Complete |
| ✅ Query Engine | Complete |
| ✅ Embeddings + Vector Search | Complete |
| ✅ LLM Orchestration | Complete |
| ✅ MCP Server | Complete |
| ✅ VS Code Extension | Complete |

## License

MIT

## Community

- [Discord](https://discord.gg/codecompass)
- [Twitter/X](https://twitter.com/codecompass)
- [GitHub Issues](https://github.com/codecompass/codecompass/issues)

---

**Made with ❤️ by developers, for developers.**
