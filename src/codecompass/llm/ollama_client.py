"""Ollama client (local, privacy-first default).

Talks to Ollama's HTTP API directly with httpx so streaming can be mocked in
tests without running a server. Nothing leaves the machine.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from codecompass.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClient,
    LLMError,
)

logger = logging.getLogger("codecompass.llm")

DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaClient(LLMClient):
    """Streaming chat client for a local Ollama server."""

    provider = "ollama"
    is_cloud = False

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def stream(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Yield tokens from ``POST /api/chat`` (newline-delimited JSON)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        logger.info("Ollama request: model=%s", self.model)

        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout_seconds) as client,
                client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response,
            ):
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise LLMError(f"Ollama returned HTTP {response.status_code}: {body[:200]}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    error = data.get("error")
                    if error:
                        raise LLMError(f"Ollama error: {error}")
                    message = data.get("message") or {}
                    token = str(message.get("content") or "")
                    if token:
                        yield token
                    if data.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Could not reach Ollama at {self.base_url} "
                f"({exc.__class__.__name__}). Is it running? Start it with: ollama serve"
            ) from exc
