"""Anthropic client (cloud provider; prompts are secret-redacted first)."""

import logging
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from codecompass.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClient,
    LLMError,
)

logger = logging.getLogger("codecompass.llm")


class AnthropicClient(LLMClient):
    """Streaming client for the Anthropic Messages API."""

    provider = "anthropic"
    is_cloud = True

    def __init__(self, model: str, api_key: str, timeout_seconds: float = 60.0) -> None:
        self.model = model
        # Never log or store the key itself.
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds, max_retries=1)

    async def stream(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Yield text deltas from the Messages streaming API."""
        logger.info("Anthropic request: model=%s", self.model)
        try:
            # The 1.x SDK no longer types sampling params on stream(); they are
            # forwarded to the API through extra_body.
            async with self._client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                extra_body={"temperature": temperature},
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except LLMError:
            raise
        except Exception as exc:  # SDK raises its own error hierarchy
            raise LLMError(f"Anthropic request failed ({exc.__class__.__name__}): {exc}") from exc
