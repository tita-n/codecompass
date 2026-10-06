"""Tests for prompt assembly, context budgeting and secret redaction."""

import logging
from typing import Any

import pytest

from codecompass.llm.prompts import (
    DEFAULT_CONTEXT_TOKEN_BUDGET,
    EXPLAIN_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    SIMPLIFY_SYSTEM_PROMPT,
    assemble_context,
    assemble_explain_prompt,
    assemble_simplify_prompt,
    assemble_user_prompt,
    estimate_tokens,
)
from codecompass.llm.redaction import REDACTED, contains_secret, redact_secrets


def _chunk(
    file_path: str = "app/auth.py",
    line_start: int = 4,
    line_end: int = 8,
    symbol_name: str | None = "validate_token",
    content: str = "def validate_token(t):\n    return bool(t)",
    language: str = "python",
) -> dict[str, Any]:
    return {
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "symbol_name": symbol_name,
        "document": content,
        "language": language,
    }


def test_assemble_context_formats_markdown_headers() -> None:
    """Test a chunk becomes a headed, fenced markdown section."""
    context = assemble_context([_chunk()])

    assert "### File: app/auth.py (lines 4-8) — symbol: validate_token" in context
    assert "```python" in context
    assert "def validate_token(t):" in context
    assert context.rstrip().endswith("```")


def test_assemble_context_omits_symbol_when_unknown() -> None:
    """Test chunks without a symbol still produce a valid header."""
    context = assemble_context([_chunk(symbol_name=None)])
    assert "### File: app/auth.py (lines 4-8)" in context
    assert "symbol:" not in context


def test_assemble_context_joins_multiple_chunks() -> None:
    """Test multiple chunks are separated by a blank line."""
    context = assemble_context([_chunk(), _chunk(file_path="app/pay.py", symbol_name="charge")])
    assert context.count("### File:") == 2
    assert "\n\n### File: app/pay.py" in context


def test_assemble_context_empty() -> None:
    """Test no chunks produce no context."""
    assert assemble_context([]) == ""


def test_assemble_context_truncates_over_budget(caplog: pytest.LogCaptureFixture) -> None:
    """Test the token budget caps how much code is sent, and warns."""
    big = _chunk(content="x = 1\n" * 2000)
    with caplog.at_level(logging.WARNING, logger="codecompass.llm"):
        context = assemble_context([big, _chunk(file_path="other.py")], token_budget=50)

    assert "other.py" not in context  # dropped to respect the budget
    assert "context truncated" in context
    assert any("Context truncated" in record.message for record in caplog.records)


def test_default_token_budget_is_documented() -> None:
    """Test the default budget is the documented 8k tokens."""
    assert DEFAULT_CONTEXT_TOKEN_BUDGET == 8_000
    assert estimate_tokens("a" * 400) == 100


def test_assemble_user_prompt_contains_question_and_context() -> None:
    """Test the user prompt carries both the question and the context."""
    prompt = assemble_user_prompt("Where is validation?", "CONTEXT-BLOCK")
    assert "Where is validation?" in prompt
    assert "CONTEXT-BLOCK" in prompt
    assert prompt.index("Where is validation?") < prompt.index("CONTEXT-BLOCK")


def test_assemble_code_prompts_include_range_and_code() -> None:
    """Test explain/simplify prompts include the file, range and code."""
    for prompt in (
        assemble_explain_prompt("app/a.py", 3, 7, "x = 1", "python"),
        assemble_simplify_prompt("app/a.py", 3, 7, "x = 1", "python"),
    ):
        assert "app/a.py (lines 3-7)" in prompt
        assert "```python\nx = 1\n```" in prompt


def test_system_prompts_encode_citation_and_refusal_rules() -> None:
    """Test the QA prompt demands citations and an explicit refusal."""
    assert "ONLY the provided context" in QA_SYSTEM_PROMPT
    assert "app/auth.py:4-18" in QA_SYSTEM_PROMPT
    assert "I don't have enough information" in QA_SYSTEM_PROMPT
    assert "Markdown" in QA_SYSTEM_PROMPT
    # Explain must not rewrite code; simplify must stay read-only.
    assert "Do NOT rewrite" in EXPLAIN_SYSTEM_PROMPT
    assert "READ-ONLY" in SIMPLIFY_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "secret",
    [
        'api_key = "sk-abcdefghijklmnopqrstuvwx"',
        "OPENAI_API_KEY=sk-0123456789abcdefghij",
        "aws_token = AKIAIOSFODNN7EXAMPLE",
        'password: "hunter2secret"',
        "client_secret = abcdef123456xyz",
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
    ],
)
def test_redact_secrets_masks_common_shapes(secret: str) -> None:
    """Test credential-shaped strings are masked before leaving the machine."""
    redacted = redact_secrets(secret)
    assert REDACTED in redacted
    assert contains_secret(secret)
    assert not contains_secret(redacted)


def test_redact_secrets_preserves_surrounding_code() -> None:
    """Test redaction keeps the code readable."""
    source = 'def load():\n    return Config(api_key = "sk-abcdefghijklmnopqrstuvwx")\n'
    redacted = redact_secrets(source)
    assert "def load():" in redacted
    assert "Config(api_key = " in redacted
    assert "sk-abcdefghijklmnopqrstuvwx" not in redacted


def test_redact_secrets_leaves_clean_text_untouched() -> None:
    """Test ordinary code is not modified."""
    source = "def add(a: int, b: int) -> int:\n    return a + b\n"
    assert redact_secrets(source) == source
    assert not contains_secret(source)
    assert redact_secrets("") == ""
