"""ChromaDB vector store integration.

Wraps a persistent ChromaDB collection (cosine distance) that holds embedded
code chunks together with their metadata. IDs are deterministic
(``chunk:<sqlite id>``) and writes use ``upsert`` so re-indexing a file is
idempotent; stale entries are removed with :meth:`delete_by_file`.
"""

import uuid
from pathlib import Path
from typing import Any

import chromadb
import numpy as np
from chromadb.config import Settings

DEFAULT_COLLECTION = "codecompass"


class VectorStore:
    """Persistent ChromaDB collection for code chunks."""

    def __init__(self, persist_directory: str | Path, collection_name: str = DEFAULT_COLLECTION):
        self.persist_directory = str(persist_directory)
        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(
            path=self.persist_directory,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: list[dict], embeddings: np.ndarray | list) -> list[str]:
        """Add (or upsert) chunks with their embeddings to the collection.

        Args:
            chunks: Chunk dicts with keys ``file_path``, ``symbol_name``,
                ``line_start``, ``line_end``, ``content``, ``language`` and
                ``chunk_index``. Optional ``id`` (used to build a stable
                Chroma ID), ``chunk_id`` (the SQLite row id) and ``repo_root``
                are stored as metadata for merging search results.
            embeddings: Array of shape ``(len(chunks), dim)`` or an equal
                length list of embedding vectors.

        Returns:
            The list of Chroma IDs assigned to the chunks.
        """
        if not chunks:
            return []

        embeddings_list = (
            embeddings.tolist() if isinstance(embeddings, np.ndarray) else list(embeddings)
        )
        if len(embeddings_list) != len(chunks):
            raise ValueError(f"Got {len(embeddings_list)} embeddings for {len(chunks)} chunks")

        ids: list[str] = []
        # Chroma validates metadata value types at runtime; the union its
        # stubs expect is verbose, so keep this list loosely typed.
        metadatas: list[Any] = []
        documents: list[str] = []
        for chunk, _vector in zip(chunks, embeddings_list, strict=True):
            chunk_id = chunk.get("id", chunk.get("chunk_id"))
            ids.append(f"chunk:{chunk_id}" if chunk_id is not None else uuid.uuid4().hex)
            metadata: dict[str, Any] = {
                "file_path": str(chunk["file_path"]),
                "symbol_name": str(chunk.get("symbol_name") or ""),
                "line_start": int(chunk["line_start"]),
                "line_end": int(chunk["line_end"]),
                "language": str(chunk.get("language") or ""),
                "chunk_index": int(chunk.get("chunk_index", 0)),
                "repo_root": str(chunk.get("repo_root") or ""),
            }
            if chunk.get("chunk_id") is not None:
                metadata["chunk_id"] = int(chunk["chunk_id"])
            metadatas.append(metadata)
            documents.append(str(chunk["content"]))

        self.collection.upsert(
            ids=ids,
            embeddings=embeddings_list,
            metadatas=metadatas,
            documents=documents,
        )
        return ids

    def search(
        self,
        query_embedding: np.ndarray,
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """Search for chunks similar to ``query_embedding``.

        Args:
            query_embedding: The query vector.
            n_results: Maximum number of hits to return.
            where: Optional Chroma metadata filter, e.g.
                ``{"file_path": {"$eq": "app/auth.py"}}``.

        Returns:
            List of dicts with keys ``id``, ``document``, ``metadata`` and
            ``distance`` (cosine distance; lower is more similar).
        """
        total = self.collection.count()
        if total == 0 or n_results <= 0:
            return []

        results = self.collection.query(
            query_embeddings=[np.asarray(query_embedding, dtype=float).tolist()],
            n_results=min(n_results, total),
            where=where,
            include=["metadatas", "documents", "distances"],
        )

        hits: list[dict] = []
        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        for i, hit_id in enumerate(ids):
            hits.append(
                {
                    "id": hit_id,
                    "document": documents[i],
                    "metadata": metadatas[i],
                    "distance": distances[i],
                }
            )
        return hits

    def delete_by_file(self, file_path: str, repo_root: str | None = None) -> None:
        """Delete all chunks for a given repo-relative file path."""
        where: dict = {"file_path": {"$eq": file_path}}
        if repo_root:
            where = {"$and": [where, {"repo_root": {"$eq": repo_root}}]}
        self.collection.delete(where=where)

    def delete_all(self) -> None:
        """Delete every chunk in the collection."""
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        """Return the number of chunks in the collection."""
        return self.collection.count()
