"""Tests for the ChromaDB vector store."""

from pathlib import Path

import numpy as np
import pytest

from codecompass.vector.store import VectorStore


def _chunk(
    chunk_id: int,
    file_path: str = "app/auth.py",
    content: str = "def validate_token():\n    return True",
    repo_root: str = "/repo",
) -> dict:
    return {
        "id": chunk_id,
        "chunk_id": chunk_id,
        "file_path": file_path,
        "symbol_name": "validate_token",
        "line_start": 1,
        "line_end": 2,
        "language": "python",
        "chunk_index": 0,
        "content": content,
        "repo_root": repo_root,
    }


def test_add_and_search_orders_by_similarity(tmp_path: Path) -> None:
    """Test hits come back nearest-first with metadata and distance."""
    store = VectorStore(tmp_path / "chroma")
    chunks = [
        _chunk(1, content="def validate_token():\n    return True"),
        _chunk(2, file_path="app/pay.py", content="def charge(amount):\n    ..."),
    ]
    embeddings = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)

    ids = store.add_chunks(chunks, embeddings)

    assert ids == ["chunk:1", "chunk:2"]
    assert store.count() == 2

    hits = store.search(np.array([1.0, 0.1, 0.0]), n_results=2)

    assert len(hits) == 2
    assert hits[0]["id"] == "chunk:1"
    assert hits[0]["metadata"]["file_path"] == "app/auth.py"
    assert hits[0]["metadata"]["chunk_id"] == 1
    assert hits[0]["metadata"]["symbol_name"] == "validate_token"
    assert hits[0]["distance"] < hits[1]["distance"]
    assert "validate_token" in hits[0]["document"]


def test_search_on_empty_store(tmp_path: Path) -> None:
    """Test searching an empty collection returns no hits."""
    store = VectorStore(tmp_path / "chroma")
    assert store.search(np.array([1.0, 0.0, 0.0]), n_results=5) == []


def test_search_respects_metadata_filter(tmp_path: Path) -> None:
    """Test the where filter restricts hits to a file path."""
    store = VectorStore(tmp_path / "chroma")
    chunks = [
        _chunk(1, file_path="app/auth.py"),
        _chunk(2, file_path="app/pay.py"),
        _chunk(3, file_path="app/pay.py"),
    ]
    embeddings = np.eye(3, dtype=np.float32)
    store.add_chunks(chunks, embeddings)

    hits = store.search(
        np.array([1.0, 1.0, 1.0]),
        n_results=5,
        where={"file_path": {"$eq": "app/pay.py"}},
    )

    assert len(hits) == 2
    assert {h["metadata"]["file_path"] for h in hits} == {"app/pay.py"}


def test_delete_by_file_is_repo_scoped(tmp_path: Path) -> None:
    """Test deleting one file's chunks leaves other files untouched."""
    store = VectorStore(tmp_path / "chroma")
    chunks = [
        _chunk(1, file_path="app/auth.py", repo_root="/repo"),
        _chunk(2, file_path="app/pay.py", repo_root="/repo"),
        _chunk(3, file_path="app/auth.py", repo_root="/other-repo"),
    ]
    embeddings = np.eye(3, dtype=np.float32)
    store.add_chunks(chunks, embeddings)

    store.delete_by_file("app/auth.py", repo_root="/repo")

    remaining = store.search(np.array([1.0, 1.0, 1.0]), n_results=5)
    assert store.count() == 2
    scoped = {(h["metadata"]["file_path"], h["metadata"]["repo_root"]) for h in remaining}
    assert ("app/auth.py", "/repo") not in scoped
    assert ("app/auth.py", "/other-repo") in scoped
    assert ("app/pay.py", "/repo") in scoped


def test_delete_all(tmp_path: Path) -> None:
    """Test delete_all empties the collection."""
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks([_chunk(1)], np.array([[1.0, 0.0]], dtype=np.float32))
    assert store.count() == 1

    store.delete_all()

    assert store.count() == 0
    assert store.search(np.array([1.0, 0.0]), n_results=1) == []


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    """Test re-adding the same chunk ids does not duplicate entries."""
    store = VectorStore(tmp_path / "chroma")
    chunks = [_chunk(1), _chunk(2, file_path="app/pay.py")]
    embeddings = np.eye(2, dtype=np.float32)

    store.add_chunks(chunks, embeddings)
    store.add_chunks(chunks, embeddings)

    assert store.count() == 2


def test_add_rejects_mismatched_embeddings(tmp_path: Path) -> None:
    """Test adding chunks with the wrong number of embeddings fails loudly."""
    store = VectorStore(tmp_path / "chroma")
    with pytest.raises(ValueError, match="embeddings"):
        store.add_chunks([_chunk(1)], np.zeros((2, 3), dtype=np.float32))
