"""Tests for embedding generation."""

import numpy as np
import pytest

from codecompass.vector.embedder import EMBEDDING_DIM, Embedder


class _StubModel:
    """Minimal stand-in exposing SentenceTransformer's ``encode`` interface."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: list[str], convert_to_numpy: bool = True) -> np.ndarray:
        self.calls += 1
        rows = np.arange(len(texts) * EMBEDDING_DIM, dtype=np.float32)
        return rows.reshape(len(texts), EMBEDDING_DIM)


def test_embed_batch_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test embedding a list returns an (n, 384) float32 array."""
    monkeypatch.setattr(Embedder, "_model", _StubModel())
    vectors = Embedder.embed(["first", "second", "third"])

    assert vectors.shape == (3, EMBEDDING_DIM)
    assert vectors.dtype == np.float32


def test_embed_single_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test single-text embedding returns a 1-D 384-dim vector."""
    monkeypatch.setattr(Embedder, "_model", _StubModel())
    vector = Embedder.embed_single("def validate_token()")

    assert vector.shape == (EMBEDDING_DIM,)
    assert vector.dtype == np.float32


def test_embed_empty_input_skips_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test an empty batch returns an empty array without loading the model."""
    stub = _StubModel()
    monkeypatch.setattr(Embedder, "_model", stub)
    vectors = Embedder.embed([])

    assert vectors.shape == (0, EMBEDDING_DIM)
    assert stub.calls == 0


def test_model_instance_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test repeated get_model calls reuse the same cached instance."""
    stub = _StubModel()
    monkeypatch.setattr(Embedder, "_model", stub)

    assert Embedder.get_model() is stub
    assert Embedder.get_model() is stub


def test_reset_clears_cached_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test reset() drops the cached model so it can be reloaded."""
    monkeypatch.setattr(Embedder, "_model", _StubModel())
    Embedder.reset()

    assert Embedder._model is None


@pytest.mark.slow
def test_real_model_produces_semantic_embeddings() -> None:
    """Smoke test: real all-MiniLM-L6-v2 ranks related sentences closer."""
    Embedder.reset()
    try:
        auth = Embedder.embed_single("How does user authentication work?")
        login = Embedder.embed_single("validate the login credentials and token")
        fib = Embedder.embed_single("def fibonacci(n): return n")
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"embedding model unavailable: {exc}")
    finally:
        Embedder.reset()

    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    assert auth.shape == (EMBEDDING_DIM,)
    assert cosine(auth, login) > cosine(auth, fib)
