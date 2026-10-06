"""Tests for the provider clients (fully mocked - no network, no real SDK calls)."""

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from codecompass.llm.base import LLMError, NullLLMClient
from codecompass.llm.factory import create_llm_client
from codecompass.llm.ollama_client import OllamaClient
from codecompass.utils.config import LLMConfig

OLLAMA_URL = "http://localhost:11434/api/chat"


def _ndjson(*payloads: dict[str, Any]) -> bytes:
    return b"".join((json.dumps(p) + "\n").encode() for p in payloads)


def _chunk_payload(text: str, done: bool = False) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": text}, "done": done}


# ---------------------------------------------------------------------------
# Ollama (httpx -> mocked with respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_ollama_streams_tokens() -> None:
    """Test streamed tokens are yielded in order from /api/chat."""
    route = respx.post(OLLAMA_URL)
    route.mock(
        return_value=httpx.Response(
            200,
            content=_ndjson(
                _chunk_payload("Hello "), _chunk_payload("world"), _chunk_payload("", True)
            ),
        )
    )

    client = OllamaClient(model="codellama:7b")
    tokens = [token async for token in client.stream("sys", "user question")]

    assert tokens == ["Hello ", "world"]

    payload = json.loads(route.calls.last.request.content)
    assert payload["model"] == "codellama:7b"
    assert payload["stream"] is True
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user question"},
    ]
    assert payload["options"]["temperature"] == 0.3


@respx.mock
async def test_ollama_passes_generation_options() -> None:
    """Test temperature and max_tokens reach the provider options."""
    route = respx.post(OLLAMA_URL)
    route.mock(return_value=httpx.Response(200, content=_ndjson(_chunk_payload("ok", True))))

    client = OllamaClient(model="codellama:7b")
    async for _ in client.stream("s", "u", temperature=0.9, max_tokens=128):
        pass

    options = json.loads(route.calls.last.request.content)["options"]
    assert options["temperature"] == 0.9
    assert options["num_predict"] == 128


@respx.mock
async def test_ollama_unreachable_reports_hint() -> None:
    """Test an unreachable server yields an actionable LLMError."""
    respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    client = OllamaClient(model="codellama:7b")
    with pytest.raises(LLMError, match="ollama serve"):
        async for _ in client.stream("s", "u"):
            pass


@respx.mock
async def test_ollama_http_error_is_wrapped() -> None:
    """Test HTTP failures surface as LLMError with the status code."""
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(500, text="internal boom"))

    client = OllamaClient(model="codellama:7b")
    with pytest.raises(LLMError, match="HTTP 500"):
        async for _ in client.stream("s", "u"):
            pass


@respx.mock
async def test_ollama_error_field_is_wrapped() -> None:
    """Test an in-band Ollama error message becomes an LLMError."""
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(200, content=_ndjson({"error": "model not found"}))
    )

    client = OllamaClient(model="missing")
    with pytest.raises(LLMError, match="model not found"):
        async for _ in client.stream("s", "u"):
            pass


@respx.mock
async def test_ollama_custom_base_url() -> None:
    """Test a custom endpoint (and trailing slash) is used."""
    route = respx.post("http://gpu-box:11434/api/chat")
    route.mock(return_value=httpx.Response(200, content=_ndjson(_chunk_payload("hi", True))))

    client = OllamaClient(model="m", base_url="http://gpu-box:11434/")
    tokens = [token async for token in client.stream("s", "u")]

    assert tokens == ["hi"]


# ---------------------------------------------------------------------------
# Null client (retrieval-only mode)
# ---------------------------------------------------------------------------


async def test_null_client_raises_on_stream() -> None:
    """Test the disabled provider fails with a friendly, actionable error."""
    with pytest.raises(LLMError, match="llm.provider = 'none'"):
        async for _ in NullLLMClient().stream("s", "u"):
            pass


async def test_null_client_raises_on_complete() -> None:
    """Test complete() also fails in retrieval-only mode."""
    with pytest.raises(LLMError):
        await NullLLMClient().complete("s", "u")


# ---------------------------------------------------------------------------
# Anthropic / OpenAI / Google (SDKs monkeypatched)
# ---------------------------------------------------------------------------


class _FakeAnthropicStream:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "_FakeAnthropicStream":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    @property
    def text_stream(self) -> Any:
        async def _gen() -> Any:
            yield "Hello "
            yield "world"

        return _gen()


class _FakeAnthropic:
    instances: list["_FakeAnthropic"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _FakeAnthropic.instances.append(self)
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs: Any) -> _FakeAnthropicStream:
        return _FakeAnthropicStream(kwargs)


async def test_anthropic_client_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the Anthropic client streams deltas and passes the API key."""
    monkeypatch.setattr("codecompass.llm.anthropic_client.AsyncAnthropic", _FakeAnthropic)
    _FakeAnthropic.instances.clear()

    from codecompass.llm.anthropic_client import AnthropicClient

    client = AnthropicClient(model="claude-3-5-sonnet-20241022", api_key="sk-test")
    tokens = [token async for token in client.stream("sys", "user")]

    assert tokens == ["Hello ", "world"]
    assert _FakeAnthropic.instances[0].kwargs["api_key"] == "sk-test"
    assert client.is_cloud is True


async def test_anthropic_client_wraps_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test SDK exceptions become LLMError so the CLI can fall back."""

    class _Boom:
        def __init__(self, **kwargs: Any) -> None:
            def stream(**_: Any) -> Any:
                raise RuntimeError("rate limited")

            self.messages = SimpleNamespace(stream=stream)

    monkeypatch.setattr("codecompass.llm.anthropic_client.AsyncAnthropic", _Boom)

    from codecompass.llm.anthropic_client import AnthropicClient

    client = AnthropicClient(model="m", api_key="sk-test")
    with pytest.raises(LLMError, match="Anthropic request failed"):
        async for _ in client.stream("s", "u"):
            pass


class _FakeOpenAIStream:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs

    def __aiter__(self) -> Any:
        async def _gen() -> Any:
            for text in ("Hello ", "world"):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
                )
            yield SimpleNamespace(choices=[])  # skipped: no choices

        return _gen()


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _FakeOpenAI.instances.append(self)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: Any) -> _FakeOpenAIStream:
        return _FakeOpenAIStream(kwargs)


async def test_openai_client_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the OpenAI client streams chat completion deltas."""
    monkeypatch.setattr("codecompass.llm.openai_client.AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.instances.clear()

    from codecompass.llm.openai_client import OpenAIClient

    client = OpenAIClient(model="gpt-4o-mini", api_key="sk-test")
    tokens = [token async for token in client.stream("sys", "user")]

    assert tokens == ["Hello ", "world"]
    assert _FakeOpenAI.instances[0].kwargs["api_key"] == "sk-test"
    assert client.is_cloud is True


class _FakeGoogleChunk:
    def __init__(self, text: str | None) -> None:
        self._text = text

    @property
    def text(self) -> str:
        if self._text is None:
            raise ValueError("no text in candidate")
        return self._text


class _FakeGoogleModel:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def generate_content_async(self, content: str, **kwargs: Any) -> Any:
        async def _gen() -> Any:
            yield _FakeGoogleChunk("Hello ")
            yield _FakeGoogleChunk(None)  # blocked chunk -> skipped
            yield _FakeGoogleChunk("world")

        return _gen()


class _FakeGenai:
    configured: dict[str, Any] = {}

    class GenerationConfig:  # stand-in for the SDK config object
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    @staticmethod
    def configure(**kwargs: Any) -> None:
        _FakeGenai.configured.update(kwargs)

    @staticmethod
    def GenerativeModel(**kwargs: Any) -> _FakeGoogleModel:  # SDK naming
        return _FakeGoogleModel(**kwargs)


async def test_google_client_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the Google client streams text and skips blocked chunks."""
    monkeypatch.setattr("codecompass.llm.google_client.genai", _FakeGenai)
    _FakeGenai.configured.clear()

    from codecompass.llm.google_client import GoogleClient

    client = GoogleClient(model="gemini-1.5-flash", api_key="key-test")
    tokens = [token async for token in client.stream("sys", "user")]

    assert tokens == ["Hello ", "world"]
    assert _FakeGenai.configured["api_key"] == "key-test"
    assert client.is_cloud is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_returns_null_client_for_none() -> None:
    """Test provider 'none' yields retrieval-only mode."""
    assert isinstance(create_llm_client(LLMConfig(provider="none")), NullLLMClient)


def test_factory_builds_ollama_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the Ollama client honours config and the OLLAMA_HOST env var."""
    client = create_llm_client(LLMConfig(provider="ollama", model="llama3"))
    assert isinstance(client, OllamaClient)
    assert client.base_url == "http://localhost:11434"

    monkeypatch.setenv("OLLAMA_HOST", "http://remote:11434")
    client = create_llm_client(LLMConfig(provider="ollama", model="llama3"))
    assert isinstance(client, OllamaClient)
    assert client.base_url == "http://remote:11434"


def test_factory_rejects_unknown_provider() -> None:
    """Test an unknown provider fails with the list of valid ones."""
    with pytest.raises(LLMError, match="Unknown LLM provider"):
        create_llm_client(LLMConfig(provider="hal9000"))


def test_factory_requires_key_for_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test cloud providers without a key fail with actionable guidance."""
    monkeypatch.setattr("codecompass.llm.factory.get_api_key", lambda provider: None)

    with pytest.raises(LLMError, match="config set-key anthropic"):
        create_llm_client(LLMConfig(provider="anthropic", model="claude-3-5-sonnet"))


def test_factory_builds_anthropic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test a configured cloud provider yields its SDK client."""
    monkeypatch.setattr("codecompass.llm.factory.get_api_key", lambda provider: "sk-test")
    monkeypatch.setattr("codecompass.llm.anthropic_client.AsyncAnthropic", _FakeAnthropic)

    from codecompass.llm.anthropic_client import AnthropicClient

    client = create_llm_client(LLMConfig(provider="anthropic", model="claude-3-5-sonnet"))
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-3-5-sonnet"
