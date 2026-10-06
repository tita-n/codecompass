"""Configuration management for CodeCompass."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "llm": {
        "provider": "ollama",  # none | ollama | anthropic | openai | google
        "model": "codellama:7b",
        "temperature": 0.3,
        "max_tokens": 2048,
        "stream": True,
        "timeout_seconds": 60,
        "base_url": None,  # Ollama endpoint override
    },
    "indexing": {
        "exclude_dirs": [".git", "node_modules", "dist", "target", "venv", "__pycache__"],
        "max_file_size_mb": 10,
        "incremental": True,
    },
    "storage": {
        "base_dir": "~/.codecompass",
        "sqlite_path": "codecompass.db",
        "chroma_path": "chroma_db",
    },
    "performance": {
        "max_workers": 4,
        "chunk_size": 500,
        "chunk_overlap": 200,
    },
}


class LLMConfig(BaseModel):
    """LLM provider settings.

    API keys are deliberately *not* part of this model: they live in the OS
    keyring (see :func:`set_api_key`) or the environment, never in config.yaml.
    """

    provider: str = "ollama"  # none | ollama | anthropic | openai | google
    model: str = "codellama:7b"
    temperature: float = 0.3
    max_tokens: int = 2048
    stream: bool = True
    timeout_seconds: int = 60
    base_url: str | None = None  # Ollama endpoint (default http://localhost:11434)


class IndexingConfig(BaseModel):
    exclude_dirs: list[str] = [".git", "node_modules", "dist", "target", "venv", "__pycache__"]
    max_file_size_mb: int = 10
    incremental: bool = True


class StorageConfig(BaseModel):
    base_dir: str = "~/.codecompass"
    sqlite_path: str = "codecompass.db"
    chroma_path: str = "chroma_db"


class PerformanceConfig(BaseModel):
    max_workers: int = 4
    chunk_size: int = 500
    chunk_overlap: int = 200


class Config(BaseModel):
    llm: LLMConfig = LLMConfig()
    indexing: IndexingConfig = IndexingConfig()
    storage: StorageConfig = StorageConfig()
    performance: PerformanceConfig = PerformanceConfig()

    @property
    def config_path(self) -> Path:
        return Path.home() / ".codecompass" / "config.yaml"

    @property
    def base_dir(self) -> Path:
        return Path(os.path.expanduser(self.storage.base_dir))

    def get_sqlite_path(self) -> Path:
        return self.base_dir / self.storage.sqlite_path

    def get_chroma_path(self) -> Path:
        return self.base_dir / self.storage.chroma_path


#: Environment override for ``llm.provider``. The VS Code extension sets it
#: when spawning the daemon so the "LLM Provider" setting wins for that
#: process only (CLI and MCP keep reading config.yaml).
LLM_PROVIDER_ENV = "CODECOMPASS_LLM_PROVIDER"


def get_config() -> Config:
    """Load configuration from file or create default.

    ``CODECOMPASS_LLM_PROVIDER`` (exported by the VS Code extension) overrides
    ``llm.provider`` for the current process.
    """
    config_path = Path.home() / ".codecompass" / "config.yaml"

    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
            config = Config(**data)
    else:
        config = Config()

    provider = os.environ.get(LLM_PROVIDER_ENV, "").strip().lower()
    if provider:
        config.llm.provider = provider
    return config


def init_config() -> Path:
    """Initialize configuration file with defaults."""
    config_dir = Path.home() / ".codecompass"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"

    if not config_path.exists():
        with open(config_path, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False)

    # Create storage directories
    config = Config()
    config.base_dir.mkdir(parents=True, exist_ok=True)

    return config_path


# ---------------------------------------------------------------------------
# API key storage (OS keyring, never plaintext in config.yaml)
# ---------------------------------------------------------------------------

#: Keyring service prefix; the full service name is ``codecompass:<provider>``.
KEYRING_SERVICE_PREFIX = "codecompass"

#: Providers for which an API key can be stored.
KEY_PROVIDERS = ("anthropic", "openai", "google")

#: Environment variables checked (in order) as a fallback for each provider.
PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


def keyring_service(provider: str) -> str:
    """Return the keyring service name used for ``provider``."""
    return f"{KEYRING_SERVICE_PREFIX}:{provider.strip().lower()}"


def set_api_key(provider: str, key: str) -> None:
    """Store an API key for ``provider`` in the OS keyring.

    Args:
        provider: One of :data:`KEY_PROVIDERS`.
        key: The secret value (never written to disk by CodeCompass).

    Raises:
        ValueError: If the provider is unsupported or the key is empty.
        RuntimeError: If the OS keyring is unavailable.
    """
    normalized = provider.strip().lower()
    if normalized not in KEY_PROVIDERS:
        raise ValueError(
            f"Cannot store keys for provider '{provider}'. Supported: {', '.join(KEY_PROVIDERS)}"
        )
    secret = key.strip()
    if not secret:
        raise ValueError("API key must not be empty")

    import keyring  # imported lazily: only needed when managing secrets

    try:
        keyring.set_password(keyring_service(normalized), "api_key", secret)
    except Exception as exc:  # backend missing / locked / no dbus
        raise RuntimeError(
            f"Could not access the OS keyring: {exc}. "
            f"Alternatively export {normalized.upper()}_API_KEY in your environment."
        ) from exc


def get_api_key(provider: str) -> str | None:
    """Return the API key for ``provider``.

    Looks in the OS keyring first, then in provider-specific environment
    variables. Returns ``None`` when nothing is configured. Keyring problems
    (e.g. no keyring daemon) are treated as "no key stored" so reads never
    crash the CLI.
    """
    normalized = provider.strip().lower()

    import keyring  # imported lazily: only needed when resolving secrets

    try:
        stored = keyring.get_password(keyring_service(normalized), "api_key")
    except Exception:
        stored = None
    if stored:
        return stored

    for env_var in PROVIDER_ENV_VARS.get(normalized, ()):
        value = os.environ.get(env_var)
        if value:
            return value
    return None


def has_api_key(provider: str) -> bool:
    """True if an API key is available (keyring or environment)."""
    return get_api_key(provider) is not None
