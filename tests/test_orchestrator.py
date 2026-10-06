"""Tests for the LLM orchestrator (prompt wiring, streaming, redaction)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from codecompass.llm.base import LLMClient, LLMError
from codecompass.llm.orchestrator import Orchestrator
from codecompass.llm.prompts import EXPLAIN_SYSTEM_PROMPT, QA_SYSTEM_PROMPT, SIMPLIFY_SYSTEM_PROMPT
from tests.conftest import FakeLLMClient

CHUNK: dict[str, Any] = {
    "file_path": "app/auth.py",
    "line_start": 4,
    "line_end": 8,
    "symbol_name": "validate_token",
    "language": "python",
    "document": "def validate_token(t):\n    return bool(t)",
}


class _CloudFakeClient(FakeLLMClient):
    """A fake cloud client: inputs must be redacted before sending."""

    provider = "cloud-fake"
    is_cloud = True


class _FailingClient(LLMClient):
    provider = "failing"

    async def _fail(self) -> None:
        raise LLMError("provider exploded")

    def stream(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Async generator that fails on its first step."""
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        await self._fail()
        yield ""  # pragma: no cover - unreachable at runtime


async def test_answer_question_streams_tokens() -> None:
    """Test the orchestrator yields the client's tokens unchanged."""
    client = FakeLLMClient("Payment validation lives in validate_payment.")
    orchestrator = Orchestrator(client)

    tokens = [token async for token in orchestrator.answer_question("where?", [CHUNK])]

    assert "".join(tokens).strip() == "Payment validation lives in validate_payment."
    assert len(tokens) > 1  # streamed token by token, not one blob


async def test_answer_question_sends_qa_prompt_with_context() -> None:
    """Test the QA system prompt and a context block are passed to the client."""
    client = FakeLLMClient()
    orchestrator = Orchestrator(client)

    async for _ in orchestrator.answer_question("where is validation?", [CHUNK]):
        pass

    call = client.calls[0]
    assert call["system"] == QA_SYSTEM_PROMPT
    assert "where is validation?" in call["user"]
    assert "### File: app/auth.py (lines 4-8)" in call["user"]
    assert "def validate_token(t):" in call["user"]


async def test_explain_and_simplify_use_their_prompts() -> None:
    """Test explain/simplify use their own system prompts and code slices."""
    explain_client = FakeLLMClient("It validates a token.")
    async for _ in Orchestrator(explain_client).explain("x = 1", "app/a.py", 1, 1, "python"):
        pass
    assert explain_client.calls[0]["system"] == EXPLAIN_SYSTEM_PROMPT
    assert "x = 1" in explain_client.calls[0]["user"]
    assert "app/a.py (lines 1-1)" in explain_client.calls[0]["user"]

    simplify_client = FakeLLMClient("Simpler version below.")
    async for _ in Orchestrator(simplify_client).simplify("x = 1", "app/a.py", 1, 1, "python"):
        pass
    assert simplify_client.calls[0]["system"] == SIMPLIFY_SYSTEM_PROMPT


async def test_cloud_provider_redacts_secrets_before_sending() -> None:
    """Test code sent to a cloud provider has apparent secrets redacted."""
    leaky = dict(CHUNK)
    leaky["document"] = 'API_KEY = "sk-abcdefghijklmnopqrstuvwx"\n'
    client = _CloudFakeClient()
    orchestrator = Orchestrator(client)

    async for _ in orchestrator.answer_question("where?", [leaky]):
        pass

    sent = client.calls[0]["user"]
    assert "sk-abcdefghijklmnopqrstuvwx" not in sent
    assert "[REDACTED]" in sent


async def test_local_provider_does_not_redact() -> None:
    """Test local providers (is_cloud=False) get the raw code."""
    leaky = dict(CHUNK)
    leaky["document"] = 'API_KEY = "sk-abcdefghijklmnopqrstuvwx"\n'
    client = FakeLLMClient()
    orchestrator = Orchestrator(client)

    async for _ in orchestrator.answer_question("where?", [leaky]):
        pass

    assert "sk-abcdefghijklmnopqrstuvwx" in client.calls[0]["user"]


async def test_provider_errors_propagate() -> None:
    """Test provider errors surface as LLMError for the CLI to handle."""
    orchestrator = Orchestrator(_FailingClient())
    with pytest.raises(LLMError, match="provider exploded"):
        async for _ in orchestrator.answer_question("q", [CHUNK]):
            pass
