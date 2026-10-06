"""Sentence-transformer based embedding generation.

Uses the lightweight ``all-MiniLM-L6-v2`` model (384 dimensions, ~80 MB,
CPU-friendly) via SentenceTransformers. The model is loaded lazily on first
use and cached for the lifetime of the process so repeated indexing runs and
queries do not pay the load cost twice.
"""

import threading
from typing import Any

import numpy as np

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class Embedder:
    """Lazy, cached wrapper around a SentenceTransformer model."""

    _model: Any = None
    _lock = threading.Lock()

    @classmethod
    def get_model(cls, model_name: str = DEFAULT_MODEL_NAME) -> Any:
        """Return the shared model instance, loading it on first use."""
        with cls._lock:
            if cls._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:  # pragma: no cover - env dependent
                    raise RuntimeError(
                        "sentence-transformers is required for embeddings. "
                        "Install it with: pip install sentence-transformers"
                    ) from exc
                cls._model = SentenceTransformer(model_name)
            return cls._model

    @classmethod
    def embed(cls, texts: list[str]) -> np.ndarray:
        """Generate embeddings for a list of texts.

        Returns:
            A float32 array of shape ``(len(texts), EMBEDDING_DIM)``.
        """
        if not texts:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        model = cls.get_model()
        encoded = model.encode(list(texts), convert_to_numpy=True)
        return np.asarray(encoded, dtype=np.float32)

    @classmethod
    def embed_single(cls, text: str) -> np.ndarray:
        """Generate an embedding for a single text (shape ``(EMBEDDING_DIM,)``)."""
        return np.asarray(cls.embed([text])[0], dtype=np.float32)

    @classmethod
    def reset(cls) -> None:
        """Drop the cached model (used by tests to control model loading)."""
        with cls._lock:
            cls._model = None
