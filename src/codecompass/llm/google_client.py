"""Google Gemini client (cloud provider; prompts are secret-redacted first)."""

import logging
import warnings
from collections.abc import AsyncIterator

with warnings.catch_warnings():
    # google-generativeai is deprecated upstream and emits a FutureWarning on
    # import; it is the dependency pinned for this phase, so keep CLI output
    # clean rather than letting the warning leak to users.
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai

from codecompass.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClient,
    LLMError,
)

logger = logging.getLogger("codecompass.llm")


class GoogleClient(LLMClient):
    """Streaming client for Google Gemini via google-generativeai."""

    provider = "google"
    is_cloud = True

    def __init__(self, model: str, api_key: str, timeout_seconds: float = 60.0) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        # Never log or store the key itself.
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name=model)

    async def stream(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Yield text deltas from ``generate_content_async(stream=True)``."""
        logger.info("Google request: model=%s", self.model)
        generation_config = genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        try:
            response = await self._model.generate_content_async(
                user,
                generation_config=generation_config,
                stream=True,
            )
            async for chunk in response:
                try:
                    text = chunk.text
                except ValueError:
                    # Blocked / empty candidate: nothing to emit.
                    logger.warning("Google returned a chunk with no text payload")
                    continue
                if text:
                    yield text
        except LLMError:
            raise
        except Exception as exc:  # SDK raises its own error hierarchy
            raise LLMError(f"Google request failed ({exc.__class__.__name__}): {exc}") from exc
