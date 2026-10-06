"""Vector search components for CodeCompass (embeddings + ChromaDB store)."""

from typing import TYPE_CHECKING, Any

from codecompass.vector.embedder import Embedder

if TYPE_CHECKING:
    from codecompass.vector.store import VectorStore

__all__ = ["Embedder", "VectorStore"]


def __getattr__(name: str) -> Any:
    # ChromaDB is heavy (~2s import), so defer it until actually requested.
    if name == "VectorStore":
        from codecompass.vector.store import VectorStore

        return VectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
