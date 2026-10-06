"""Orchestrator: turns retrieved code into a streamed, cited answer.

Single-turn by design (no conversation history yet). Retrieved chunks are the
only source of truth, and every answer is accompanied by the citations table
built from those same chunks, so a model cannot invent a file path that the
retrieval layer did not actually find.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from codecompass.llm.base import LLMClient
from codecompass.llm.prompts import (
    EXPLAIN_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    SIMPLIFY_SYSTEM_PROMPT,
    assemble_context,
    assemble_explain_prompt,
    assemble_simplify_prompt,
    assemble_user_prompt,
)
from codecompass.llm.redaction import contains_secret, redact_secrets

logger = logging.getLogger("codecompass.llm")


class Orchestrator:
    """Drives prompt assembly, provider streaming and secret redaction."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def _prepare(self, user_prompt: str) -> str:
        """Redact apparent secrets when talking to a cloud provider."""
        if self.client.is_cloud and contains_secret(user_prompt):
            logger.warning(
                "Redacted apparent secrets before sending code to provider=%s",
                self.client.provider,
            )
            return redact_secrets(user_prompt)
        return user_prompt

    async def answer_question(
        self, question: str, chunks: list[dict[str, Any]]
    ) -> AsyncIterator[str]:
        """Stream an answer to ``question`` grounded in ``chunks``."""
        context = assemble_context(chunks)
        user_prompt = self._prepare(assemble_user_prompt(question, context))
        logger.info(
            "answer_question: provider=%s model=%s chunks=%d",
            self.client.provider,
            self.client.model,
            len(chunks),
        )
        async for token in self.client.stream(QA_SYSTEM_PROMPT, user_prompt):
            yield token

    async def explain(
        self,
        code: str,
        file_path: str,
        line_start: int,
        line_end: int,
        language: str = "text",
    ) -> AsyncIterator[str]:
        """Stream an explanation of a code block."""
        user_prompt = self._prepare(
            assemble_explain_prompt(file_path, line_start, line_end, code, language)
        )
        async for token in self.client.stream(EXPLAIN_SYSTEM_PROMPT, user_prompt):
            yield token

    async def simplify(
        self,
        code: str,
        file_path: str,
        line_start: int,
        line_end: int,
        language: str = "text",
    ) -> AsyncIterator[str]:
        """Stream a read-only simplification preview for a code block."""
        user_prompt = self._prepare(
            assemble_simplify_prompt(file_path, line_start, line_end, code, language)
        )
        async for token in self.client.stream(SIMPLIFY_SYSTEM_PROMPT, user_prompt):
            yield token
