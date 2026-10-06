"""OpenAI client (cloud provider; prompts are secret-redacted first)."""

import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from openai import AsyncOpenAI, AsyncStream

from codecompass.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClient,
    LLMError,
)

logger = logging.getLogger("codecompass.llm")


class OpenAIClient(LLMClient):
    """Streaming client for the OpenAI Chat Completions API."""

    provider = "openai"
    is_cloud = True

    def __init__(self, model: str, api_key: str, timeout_seconds: float = 60.0) -> None:
        self.model = model
        # Never log or store the key itself.
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=1)

    async def stream(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Yield content deltas from Chat Completions streaming."""
        logger.info("OpenAI request: model=%s", self.model)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            # With stream=True the SDK returns the AsyncStream directly.
            stream = cast(AsyncStream[Any], response)
            async for event in stream:
                choices = getattr(event, "choices", None) or []
                if not choices:
                    continue
                token = getattr(choices[0].delta, "content", None)
                if token:
                    yield token
        except LLMError:
            raise
        except Exception as exc:  # SDK raises its own error hierarchy
            raise LLMError(f"OpenAI request failed ({exc.__class__.__name__}): {exc}") from exc
