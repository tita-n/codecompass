"""Tests for configuration management."""

from pathlib import Path

import pytest
import yaml

from codecompass.utils.config import (
    DEFAULT_CONFIG,
    Config,
    LLMConfig,
    get_api_key,
    get_config,
    has_api_key,
    init_config,
    keyring_service,
    set_api_key,
)


def test_config_default() -> None:
    """Test default config values."""
    config = Config()
    assert config.llm.provider == "ollama"
    assert config.llm.model == "codellama:7b"
    assert config.indexing.max_file_size_mb == 10
    assert config.performance.chunk_size == 500


def test_config_from_dict() -> None:
    """Test config from dictionary."""
    from codecompass.utils.config import IndexingConfig

    llm = LLMConfig(provider="anthropic", model="claude-3-5-sonnet")
    indexing = IndexingConfig(max_file_size_mb=20)
    config = Config(llm=llm, indexing=indexing)
    assert config.llm.provider == "anthropic"
    assert config.llm.model == "claude-3-5-sonnet"
    assert config.indexing.max_file_size_mb == 20


def test_init_config_creates_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that init_config creates necessary directories."""
    # Mock home directory to a temp path
    mock_home = tmp_path / "home"
    mock_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: mock_home)

    config_path = init_config()
    assert config_path.exists()
    assert config_path.parent.exists()

    # Check storage directory was created
    config = get_config()
    assert config.base_dir.exists()

    # Validate config file content is valid YAML
    with open(config_path) as f:
        data = yaml.safe_load(f)
        assert "llm" in data
        assert "indexing" in data
        assert data["llm"]["provider"] == "ollama"


def test_llm_config_has_no_plaintext_api_key() -> None:
    """Test API keys are not part of the persisted config model."""
    config = Config()
    assert config.llm.provider == "ollama"
    assert config.llm.stream is True
    assert config.llm.timeout_seconds == 60
    assert "api_key" not in LLMConfig.model_fields
    assert "api_key" not in config.llm.model_dump()
    assert "api_key" not in DEFAULT_CONFIG["llm"]


def test_keyring_service_naming() -> None:
    """Test keyring services are namespaced per provider."""
    assert keyring_service("Anthropic") == "codecompass:anthropic"


def test_set_and_get_api_key_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test keys round-trip through the (mocked) keyring."""
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        "keyring.set_password", lambda service, user, pw: store.__setitem__((service, user), pw)
    )
    monkeypatch.setattr("keyring.get_password", lambda service, user: store.get((service, user)))

    set_api_key("openai", "sk-secret")
    assert get_api_key("openai") == "sk-secret"
    assert has_api_key("openai") is True
    assert store[("codecompass:openai", "api_key")] == "sk-secret"


def test_set_api_key_rejects_bad_input() -> None:
    """Test unsupported providers and empty keys are rejected."""
    with pytest.raises(ValueError, match="Cannot store keys"):
        set_api_key("ollama", "x")
    with pytest.raises(ValueError, match="must not be empty"):
        set_api_key("anthropic", "   ")


def test_get_api_key_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the environment is used when the keyring has no entry."""
    monkeypatch.setattr("keyring.get_password", lambda service, user: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    assert get_api_key("anthropic") == "env-key"
    assert has_api_key("anthropic") is True


def test_get_api_key_handles_keyring_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test a broken keyring backend degrades to 'no key' instead of crashing."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def _boom(service: str, user: str) -> str:
        raise RuntimeError("no keyring daemon")

    monkeypatch.setattr("keyring.get_password", _boom)

    assert get_api_key("google") is None
    assert has_api_key("google") is False


def test_get_api_key_prefers_keyring_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the keyring wins when both sources have a value."""
    monkeypatch.setattr("keyring.get_password", lambda service, user: "keyring-key")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    assert get_api_key("openai") == "keyring-key"


def test_llm_provider_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CODECOMPASS_LLM_PROVIDER (set by the VS Code extension) wins over config.yaml."""
    from codecompass.utils.config import LLM_PROVIDER_ENV

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(LLM_PROVIDER_ENV, "Anthropic")

    assert get_config().llm.provider == "anthropic"


def test_llm_provider_env_empty_keeps_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty override leaves the configured provider alone."""
    from codecompass.utils.config import LLM_PROVIDER_ENV

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(LLM_PROVIDER_ENV, "")

    assert get_config().llm.provider == "ollama"
