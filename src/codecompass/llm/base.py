"""Provider-agnostic LLM client interface.

All providers implement :class:`LLMClient`, which streams tokens as they
arrive. Errors from any provider are normalised into :class:`LLMError` so the
CLI can degrade gracefully (e.g. fall back to showing raw retrieved chunks).
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

logger = logging.getLogger("codecompass.llm")

DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2048


class LLMError(Exception):
    """Raised when an LLM provider fails or is not configured."""


class LLMClient(ABC):
    """Abstract base for LLM providers."""

    #: Provider name used in logs and error messages.
    provider: str = "unknown"
    #: Model identifier (never a secret; safe to log).
    model: str = ""
    #: Cloud providers send source code off the machine: their inputs get
    #: secret redaction applied before sending.
    is_cloud: bool = False

    @abstractmethod
    def stream(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Yield response tokens as they arrive.

        Implemented by subclasses as an async generator (``async def`` with
        ``yield``); declared without ``async`` here, which is how type checkers
        expect asynchronous iterators to be typed.

        Raises:
            LLMError: If the provider is unreachable, unauthenticated or fails.
        """
        ...  # pragma: no cover - abstract

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """Return the full response as a single string (drains ``stream``)."""
        parts: list[str] = []
        async for token in self.stream(system, user, temperature, max_tokens):
            parts.append(token)
        return "".join(parts)


class NullLLMClient(LLMClient):
    """Retrieval-only mode: every call fails with a friendly, actionable error."""

    provider = "none"

    async def _fail(self) -> None:
        """Raise the disabled-provider error.

        Declared as returning ``None`` (rather than ``NoReturn``) so the
        ``yield`` below stays reachable for type checkers while the runtime
        behaviour is still "raise on first token request".
        """
        raise LLMError(
            "LLM is disabled (llm.provider = 'none'). Set a provider in "
            "~/.codecompass/config.yaml to get answers, or pass --no-llm to "
            "work with retrieved chunks only."
        )

    async def stream(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Always raises :class:`LLMError` when iterated."""
        await self._fail()
        yield ""  # pragma: no cover - unreachable at runtime
