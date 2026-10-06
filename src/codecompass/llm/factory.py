"""Provider factory: turn configuration into a ready-to-use LLM client.

Provider modules are imported lazily so that, for example, a local Ollama user
never pays the import cost (or dependency requirement) of a cloud SDK.
"""

import logging
import os

from codecompass.llm.base import LLMClient, LLMError, NullLLMClient
from codecompass.llm.ollama_client import DEFAULT_OLLAMA_URL, OllamaClient
from codecompass.utils.config import LLMConfig, get_api_key

logger = logging.getLogger("codecompass.llm")

#: Providers that require an API key (stored in the OS keyring or environment).
CLOUD_PROVIDERS = ("anthropic", "openai", "google")

#: All provider values accepted in configuration.
VALID_PROVIDERS = ("none", "ollama", *CLOUD_PROVIDERS)


def create_llm_client(config: LLMConfig) -> LLMClient:
    """Instantiate the client for ``config.provider``.

    Args:
        config: The ``llm`` section of the CodeCompass configuration.

    Returns:
        A ready :class:`LLMClient`, or a :class:`NullLLMClient` when the LLM
        layer is disabled (``provider: none``).

    Raises:
        LLMError: If the provider is unknown or a cloud provider has no key.
    """
    provider = (config.provider or "none").strip().lower()

    if provider == "none":
        logger.info("LLM disabled (provider=none): retrieval-only mode")
        return NullLLMClient()

    if provider == "ollama":
        base_url = config.base_url or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_URL
        logger.info("Using Ollama at %s (model=%s)", base_url, config.model)
        return OllamaClient(
            model=config.model,
            base_url=base_url,
            timeout_seconds=float(config.timeout_seconds),
        )

    if provider in CLOUD_PROVIDERS:
        api_key = get_api_key(provider)
        if not api_key:
            raise LLMError(
                f"No API key configured for '{provider}'. Store one with: "
                f"codecompass config set-key {provider} (kept in your OS keyring), "
                f"or export {provider.upper()}_API_KEY."
            )
        timeout = float(config.timeout_seconds)
        if provider == "anthropic":
            from codecompass.llm.anthropic_client import AnthropicClient

            return AnthropicClient(model=config.model, api_key=api_key, timeout_seconds=timeout)
        if provider == "openai":
            from codecompass.llm.openai_client import OpenAIClient

            return OpenAIClient(model=config.model, api_key=api_key, timeout_seconds=timeout)

        from codecompass.llm.google_client import GoogleClient

        return GoogleClient(model=config.model, api_key=api_key, timeout_seconds=timeout)

    raise LLMError(
        f"Unknown LLM provider '{provider}'. Valid providers: {', '.join(VALID_PROVIDERS)}"
    )
