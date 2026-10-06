"""Hybrid search combining vector similarity and SQLite FTS keyword matching.

Retrieval pipeline for natural-language questions over an indexed repository.
The vector side embeds the question and queries ChromaDB (cosine); the keyword
side runs an FTS5/BM25 match over the same chunks stored in SQLite. Both sides
are keyed by the chunk's SQLite row id, normalized to ``[0, 1]`` and merged
with a weighted score, so a chunk found by either signal can surface and chunks
found by both are boosted.
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

from codecompass.indexer.schema import IndexingState, get_session
from codecompass.utils.config import Config, get_config

DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_KEYWORD_WEIGHT = 0.3

# Column weights for bm25(): (content, file_path, symbol_name, language).
# Symbol names and paths are boosted so "which function handles X" style
# questions rank identically named symbols higher.
_BM25_WEIGHTS = "1.0, 0.5, 2.0, 0.5"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")

# Common English / question words stripped from keyword queries so natural-
# language questions ("Where is the X?") match on the meaningful terms only.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "you",
    }
)


def get_latest_repo_root() -> str | None:
    """Return the repo root of the most recently indexed repository, if any."""
    config = get_config()
    session = get_session(str(config.get_sqlite_path()))
    try:
        state = session.query(IndexingState).order_by(IndexingState.last_indexed_at.desc()).first()
        return state.repo_root if state is not None else None
    finally:
        session.close()


def hybrid_search(
    query: str,
    limit: int = 10,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    file_path: str | None = None,
    repo_root: str | None = None,
) -> list[dict[str, Any]]:
    """
    Perform hybrid search combining vector similarity and keyword matching.

    Args:
        query: Natural language query string.
        limit: Maximum number of results to return.
        vector_weight: Weight of the vector similarity score (0-1).
        keyword_weight: Weight of the keyword score (0-1).
        file_path: Optional repo-relative file path to restrict results to
            (absolute paths under the repository are accepted).
        repo_root: Repository to search; defaults to the most recently
            indexed repository.

    Returns:
        Ranked list of chunk dicts with keys ``id``, ``document``,
        ``file_path``, ``symbol_name``, ``line_start``, ``line_end``,
        ``language``, ``chunk_index``, ``metadata``, ``score``,
        ``vector_score`` and ``keyword_score``. The vector side is skipped
        gracefully when the embedding model or vector store is unavailable,
        so keyword-only results are still returned.
    """
    if not query.strip() or limit <= 0:
        return []

    if repo_root is None:
        repo_root = get_latest_repo_root()
        if repo_root is None:
            return []
    file_path = _normalize_file_path(file_path, repo_root)

    config = get_config()
    fetch_limit = limit * 2
    vector_results = _vector_search(query, config, repo_root, file_path, fetch_limit)
    keyword_results = _keyword_search(
        query, config.get_sqlite_path(), repo_root, file_path, fetch_limit
    )

    combined = _combine_results(vector_results, keyword_results, vector_weight, keyword_weight)
    _hydrate_from_sqlite(combined, config.get_sqlite_path())
    combined.sort(key=lambda item: (-item["score"], str(item["file_path"]), item["line_start"]))
    return combined[:limit]


def _normalize_file_path(file_path: str | None, repo_root: str) -> str | None:
    """Accept absolute paths inside the repository and return them relative."""
    if file_path is None:
        return None
    try:
        return Path(file_path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return file_path


def _vector_search(
    query: str,
    config: Config,
    repo_root: str,
    file_path: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Query ChromaDB for chunks semantically similar to ``query``."""
    if limit <= 0:
        return []
    try:
        from codecompass.vector.embedder import Embedder
        from codecompass.vector.store import VectorStore

        query_embedding = Embedder.embed_single(query)
        store = VectorStore(str(config.get_chroma_path()))
        conditions: list[dict] = [{"repo_root": {"$eq": repo_root}}]
        if file_path:
            conditions.append({"file_path": {"$eq": file_path}})
        where: dict = conditions[0] if len(conditions) == 1 else {"$and": conditions}
        hits = store.search(query_embedding, n_results=limit, where=where)
    except Exception:
        # Embedding model or vector store unavailable: fall back to keyword
        # results only (documented degradation path for offline/minimal envs).
        return []

    results: list[dict[str, Any]] = []
    for hit in hits:
        metadata = hit.get("metadata") or {}
        results.append(
            {
                "chunk_id": int(metadata["chunk_id"]) if "chunk_id" in metadata else None,
                "distance": hit.get("distance"),
                "document": hit.get("document") or "",
                "metadata": metadata,
            }
        )
    return results


def _query_tokens(query: str) -> list[str]:
    """Extract sanitized word tokens for the FTS5 MATCH expression.

    Punctuation is stripped, common stopwords are dropped and duplicates are
    removed (order-preserving).
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(query):
        lowered = token.lower()
        if lowered in _STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        tokens.append(token)
    return tokens


def _keyword_search(
    query: str,
    db_path: Path,
    repo_root: str,
    file_path: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Search the SQLite FTS index over chunks (BM25 ranked)."""
    tokens = _query_tokens(query)
    if not tokens or limit <= 0:
        return []
    match = " OR ".join(f'"{token}"' for token in tokens)

    file_clause = "AND c.file_path = ?" if file_path else ""
    sql = f"""
        SELECT c.id, c.file_path, c.symbol_name, c.line_start, c.line_end,
               c.content, c.language, c.chunk_index,
               bm25(chunks_fts, {_BM25_WEIGHTS}) AS rank
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN files f ON f.id = c.file_id
        WHERE chunks_fts MATCH ?
          AND f.repo_root = ?
          {file_clause}
        ORDER BY rank
        LIMIT ?
    """
    params: list[Any] = [match, repo_root]
    if file_path:
        params.append(file_path)
    params.append(limit)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "chunk_id": int(row[0]),
                "file_path": row[1],
                "symbol_name": row[2],
                "line_start": int(row[3]),
                "line_end": int(row[4]),
                "document": row[5],
                "language": row[6],
                "chunk_index": int(row[7]),
                "rank": float(row[8]),  # lower (more negative) is better
            }
        )
    return results


def _result_key(result: dict[str, Any]) -> Any:
    """Merge key for a result: the SQLite chunk id when available."""
    chunk_id = result.get("chunk_id")
    if chunk_id is not None:
        return chunk_id
    if result.get("id") is not None:  # Chroma-side hit without a SQLite link
        return str(result["id"])
    return f"meta:{result.get('file_path', '')}:{result.get('line_start', 0)}"


def _combine_results(
    vector_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    vector_weight: float,
    keyword_weight: float,
) -> list[dict[str, Any]]:
    """Merge vector and keyword results with weighted scores.

    Each side is min-max normalized to a ``[0, 1]`` score (vector distances:
    lower is better; BM25 ranks: lower is better) and combined as
    ``vector_weight * vector_score + keyword_score * keyword_weight``. Both
    sides are keyed by the chunk's SQLite id, so chunks found by both signals
    are boosted while chunks found by only one signal still appear.
    """
    entries: dict[Any, dict[str, Any]] = {}

    if vector_results:
        distances = [r["distance"] for r in vector_results if r.get("distance") is not None]
        max_dist = max(distances) if distances else 0.0
        min_dist = min(distances) if distances else 0.0
        span = max_dist - min_dist
        for result in vector_results:
            distance = result.get("distance")
            if distance is None:
                similarity = 0.0
            elif span < 1e-12:
                similarity = 1.0
            else:
                similarity = 1.0 - (distance - min_dist) / span
            key = _result_key(result)
            entry = entries.setdefault(
                key,
                {"vector_score": 0.0, "keyword_score": 0.0, "vector": None, "keyword": None},
            )
            entry["vector"] = result
            entry["vector_score"] = similarity

    if keyword_results:
        ranks = [r["rank"] for r in keyword_results]
        max_rank = max(ranks)
        min_rank = min(ranks)
        rank_span = max_rank - min_rank
        for result in keyword_results:
            if rank_span < 1e-12:
                keyword_score = 1.0
            else:
                keyword_score = 1.0 - (result["rank"] - min_rank) / rank_span
            key = _result_key(result)
            entry = entries.setdefault(
                key,
                {"vector_score": 0.0, "keyword_score": 0.0, "vector": None, "keyword": None},
            )
            entry["keyword"] = result
            entry["keyword_score"] = keyword_score

    combined: list[dict[str, Any]] = []
    for key, entry in entries.items():
        vector_hit: dict[str, Any] = entry["vector"] or {}
        keyword_hit: dict[str, Any] = entry["keyword"] or {}
        metadata = vector_hit.get("metadata") or {}
        base = keyword_hit if keyword_hit else {}
        combined.append(
            {
                "id": key,
                "chunk_id": (
                    entry["keyword"]["chunk_id"] if keyword_hit else vector_hit.get("chunk_id")
                ),
                "document": base.get("document", vector_hit.get("document", "")),
                "file_path": base.get("file_path", metadata.get("file_path", "")),
                "symbol_name": base.get("symbol_name", metadata.get("symbol_name") or None),
                "line_start": base.get("line_start", metadata.get("line_start", 0)),
                "line_end": base.get("line_end", metadata.get("line_end", 0)),
                "language": base.get("language", metadata.get("language", "")),
                "chunk_index": base.get("chunk_index", metadata.get("chunk_index", 0)),
                "metadata": metadata,
                "score": vector_weight * entry["vector_score"]
                + keyword_weight * entry["keyword_score"],
                "vector_score": entry["vector_score"],
                "keyword_score": entry["keyword_score"],
            }
        )
    return combined


def _hydrate_from_sqlite(results: list[dict[str, Any]], db_path: Path) -> None:
    """Overwrite result fields with the canonical chunk rows from SQLite."""
    ids = [r["chunk_id"] for r in results if isinstance(r.get("chunk_id"), int)]
    if not ids:
        return

    placeholders = ",".join("?" for _ in ids)
    sql = f"""
        SELECT id, file_path, symbol_name, line_start, line_end, content,
               language, chunk_index
        FROM chunks WHERE id IN ({placeholders})
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(sql, ids).fetchall()
    except sqlite3.Error:
        return
    finally:
        conn.close()

    by_id = {int(row[0]): row for row in rows}
    for result in results:
        chunk_id = result.get("chunk_id")
        row = by_id.get(chunk_id) if isinstance(chunk_id, int) else None
        if row is None:
            continue
        result.update(
            {
                "file_path": row[1],
                "symbol_name": row[2],
                "line_start": int(row[3]),
                "line_end": int(row[4]),
                "document": row[5],
                "language": row[6],
                "chunk_index": int(row[7]),
            }
        )
