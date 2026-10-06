# Changelog

All notable changes to CodeCompass will be documented in this file.

## [Unreleased]

### Added
- Indexing engine (Phase 1): tree-sitter parsing, symbol/call/import extraction with docstrings, signatures and line ranges, SQLite persistence, parallel indexing with progress bar, incremental indexing via file hashes, and file-watcher support
- Query engine (Phase 2): cross-file call resolution via the import graph (aliases, relative imports, wildcards, module-qualified calls), NetworkX call graph, and working CLI commands: `trace`, `callers`, `callees`, `path`, `impact`, `analyze hotspots|coupling|circular`
- Embeddings + vector search (Phase 3): code chunking (500-char chunks with 200-char overlap, attributed to the innermost enclosing symbol), local `all-MiniLM-L6-v2` embeddings via SentenceTransformers, persistent ChromaDB vector store (cosine), SQLite `chunks` table with an FTS5/BM25 keyword index kept in sync by triggers, and hybrid vector + keyword search with weighted-score fusion
- Incremental indexing re-chunks and re-embeds only changed files, and removes a file's chunks from both SQLite and ChromaDB when it is deleted
- LLM orchestration (Phase 4): provider-agnostic streaming client layer (`LLMClient`) with Ollama, Anthropic, OpenAI and Google implementations, a retrieval-only `NullLLMClient`, prompt assembly with an 8k-token context budget, and an `Orchestrator` for grounded Q&A
- Natural-language answers with citations: `codecompass query` streams the answer token by token and prints a Sources table built from the retrieved chunks, so citations can never reference a file that retrieval did not find
- `codecompass explain <file> --start N --end M` and the new `codecompass simplify <file> --start N --end M` (read-only refactor preview; no files are ever modified)
- `codecompass query --no-llm` (retrieval-only output) and `--raw` (JSON for piping)
- `codecompass config` is now a sub-app with `config set-key <provider>`, storing cloud API keys in the **OS keyring** (never in `config.yaml`, with environment-variable fallback)
- Secret redaction (`sk-...`, `api_key=...`, PEM private keys, AWS/GitHub/Slack/Stripe tokens) applied automatically before code is sent to a cloud provider
- Graceful degradation: provider errors, missing keys or an unreachable server produce a clear warning and fall back to the retrieved chunks
- MCP server (Phase 5): six read-only tools over the local index (`find_symbol`, `find_callers`, `find_callees`, `find_imports`, `get_dependency_graph`, `impact_analysis`) served over stdio and streamable HTTP, exposing Phase 1-2 intelligence to AI agents (Claude Code, Cursor, OpenCode) with exact file/line answers and no hallucinated paths
- `codecompass mcp` is now a real command with `--transport stdio|http`, `--host`, `--port` and `--list-tools`; stdio keeps stdout clean for the JSON-RPC stream
- Rotating MCP call log at `~/.codecompass/logs/mcp.log` (10 MB × 3 backups) recording tool name, arguments and duration — never file contents
- MCP documentation (`docs/mcp.md`) and a manual smoke-test guide (`scripts/mcp_smoke.md`)
- Cyclomatic complexity tracking for functions and methods during indexing
- Automatic schema migrations for databases created by earlier versions
- Local JSON-RPC daemon (`codecompass daemon`, Phase 6): the same Phase 1-4 intelligence plus `index`, `status`, `analyze_*`, and LLM streaming (`query`, `explain`, `simplify`) over stdio for editor integrations
- VS Code extension (`vscode-extension/`, Phase 6): workspace daemon client (JSON-RPC over stdio), Chat + dependency-graph webviews (fully offline, vendored `marked`/`vis-network`), Explain/Simplify, Find Callers, trace/impact from the editor, `N callers` CodeLens, status-bar health, and `index` command with progress — `npm run vendor && vsce package` to build
- Cross-platform packaging (Phase 7): PyInstaller one-file binaries for macOS (arm64/x64), Linux (x64) and Windows (x64) via `packaging/codecompass.spec` (with a tree-sitter grammar hook and runtime path hook), `packaging/build.sh` / `build.ps1`, checksummed release assets built by `.github/workflows/release.yml` on every `v*` tag, one-line installer (`packaging/install.sh`), Homebrew formula, Winget manifest, Debian package, `docs/installation.md`, and post-build smoke tests (`scripts/smoke_test.sh` / `.ps1`)
- `codecompass --version` / `-V` flag (the `version` subcommand still works)

### Changed
- Call sites are stored with the extracted callee name; unresolved (external) calls are kept with a NULL callee id so the query engine can resolve them across files
- Added `networkx`, `chromadb`, `sentence-transformers`, `anthropic`, `openai`, `google-generativeai`, `httpx`, `keyring` and `mcp` dependencies
- `llm.api_key` was removed from the config model: keys live in the OS keyring or the environment
- `llm` config gained `stream`, `timeout_seconds` and `base_url`; `provider: none` enables retrieval-only mode
- Embedding or vector-store failures degrade gracefully to keyword-only search instead of failing an index run

## [0.0.1] - 2026-08-29

### Added
- Initial project scaffolding
- CLI skeleton with all commands (placeholder)
- Configuration management with YAML
- Project structure for future phases
- CI/CD pipeline (GitHub Actions)
- Development environment setup script
- Pre-commit hooks (ruff, black, isort, mypy)
- Unit tests for config and dummy
