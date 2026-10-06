"""Shared API layer: one implementation of the intelligence handlers.

Both the MCP server (:mod:`codecompass.mcp.server`) and the stdio JSON-RPC
daemon for the VS Code extension (:mod:`codecompass.api.daemon`) dispatch into
:mod:`codecompass.api.handlers`, so agents, chat users and CLI users always see
identical answers.
"""
