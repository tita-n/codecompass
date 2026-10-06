"""Tests for hybrid (vector + keyword) search."""

import sqlite3
from pathlib import Path

import pytest

from codecompass.indexer.indexer import Indexer
from codecompass.indexer.schema import Chunk, get_session
from codecompass.query.search import (
    _combine_results,
    _keyword_search,
    _query_tokens,
    get_latest_repo_root,
    hybrid_search,
)
from codecompass.utils.config import get_config
from codecompass.vector.embedder import Embedder
from codecompass.vector.store import VectorStore

PAYMENTS_PY = '''\
"""Payments module."""


def validate_payment(amount: float) -> bool:
    """Validate a payment amount before charging the card."""
    if amount <= 0:
        return False
    return amount < 10000


def charge_card(card: str, amount: float) -> str:
    """Charge a credit card for the given amount."""
    return f"charged {amount} on {card}"
'''

AUTH_PY = '''\
"""Authentication module."""


def login(user: str, password: str) -> bool:
    """Authenticate a user session with credentials."""
    return bool(user and password)
'''


@pytest.fixture
def indexed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Index a tiny two-file repo into an isolated HOME (config-driven paths)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "payments.py").write_text(PAYMENTS_PY)
    (repo / "app" / "auth.py").write_text(AUTH_PY)
    Indexer(repo).index()
    return repo


def test_indexer_stores_chunks_fts_and_embeddings(indexed_repo: Path) -> None:
    """Chunks land in SQLite, the FTS index, and ChromaDB (all in sync)."""
    config = get_config()
    session = get_session(str(config.get_sqlite_path()))
    chunk_count = session.query(Chunk).count()
    assert chunk_count > 0

    conn = sqlite3.connect(str(config.get_sqlite_path()))
    try:
        fts_count = conn.execute("SELECT count(*) FROM chunks_fts").fetchone()
        embedded_count = conn.execute(
            "SELECT count(*) FROM chunks WHERE embedding_id IS NOT NULL"
        ).fetchone()
    finally:
        conn.close()
    assert fts_count is not None and fts_count[0] == chunk_count
    assert embedded_count is not None and embedded_count[0] == chunk_count

    store = VectorStore(str(config.get_chroma_path()))
    assert store.count() == chunk_count


def test_hybrid_search_finds_relevant_chunk(indexed_repo: Path) -> None:
    """A natural-language question ranks the matching function first."""
    results = hybrid_search("Where is payment validation logic?", limit=3)

    assert results
    top = results[0]
    assert top["file_path"] == "app/payments.py"
    assert top["symbol_name"] == "validate_payment"
    assert "validate_payment" in top["document"]
    assert 0.0 < top["score"] <= 1.0
    for key in ("line_start", "line_end", "language", "vector_score", "keyword_score"):
        assert key in top


def test_hybrid_search_respects_limit(indexed_repo: Path) -> None:
    """The limit caps the number of returned chunks."""
    results = hybrid_search("authentication login session", limit=1)
    assert len(results) == 1
    assert results[0]["file_path"] == "app/auth.py"


def test_hybrid_search_file_filter(indexed_repo: Path) -> None:
    """Filtering by relative or absolute path restricts results."""
    relative = hybrid_search("payment validation", limit=5, file_path="app/payments.py")
    assert relative
    assert {r["file_path"] for r in relative} == {"app/payments.py"}

    absolute = hybrid_search(
        "payment validation",
        limit=5,
        file_path=str(indexed_repo / "app" / "auth.py"),
    )
    assert absolute
    assert {r["file_path"] for r in absolute} == {"app/auth.py"}


def test_hybrid_search_without_index_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No indexed repository -> no results (and no exception)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert hybrid_search("anything") == []
    assert get_latest_repo_root() is None


def test_keyword_search_matches_tokens(indexed_repo: Path) -> None:
    """FTS returns BM25-ranked chunks scoped to the repository."""
    config = get_config()
    repo_root = str(indexed_repo)
    results = _keyword_search(
        "validate payment", config.get_sqlite_path(), repo_root, None, limit=5
    )

    assert results
    assert results[0]["file_path"] == "app/payments.py"
    assert results[0]["symbol_name"] == "validate_payment"
    assert isinstance(results[0]["rank"], float)


def test_query_tokens_are_sanitized() -> None:
    """FTS tokens strip punctuation/stopwords, dedupe and drop tiny words."""
    assert _query_tokens('Where is the "payment" validation??') == [
        "payment",
        "validation",
    ]
    assert _query_tokens("login login LOGIN") == ["login"]
    assert _query_tokens("!!! ...") == []


def test_combine_results_keyword_signal_flips_ranking() -> None:
    """A strong keyword hit outranks the pure-vector favorite in the merge."""
    vector = [
        {"chunk_id": 1, "distance": 0.2, "document": "a", "metadata": {}},
        {"chunk_id": 2, "distance": 0.3, "document": "b", "metadata": {}},
        {"chunk_id": 3, "distance": 0.5, "document": "c", "metadata": {}},
    ]
    keyword = [
        {"chunk_id": 2, "rank": -9.0, "document": "b", "file_path": "b.py"},
        {"chunk_id": 1, "rank": -5.0, "document": "a", "file_path": "a.py"},
    ]

    combined = _combine_results(vector, keyword, vector_weight=0.7, keyword_weight=0.3)
    order = [c["id"] for c in sorted(combined, key=lambda c: -c["score"])]

    # Pure vector order would be 1, 2, 3 -- the keyword signal promotes 2.
    assert [r["chunk_id"] for r in vector][0] == 1
    assert order[0] == 2
    assert order[1] == 1
    by_id = {c["id"]: c for c in combined}
    assert by_id[2]["keyword_score"] == 1.0
    assert by_id[3]["score"] == 0.0  # found by neither signal usefully
    assert by_id[1]["score"] > 0.0


def test_hybrid_degrades_to_keyword_only(
    indexed_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken embedding model still yields keyword results."""

    class _BrokenModel:
        def encode(self, texts: list[str], convert_to_numpy: bool = True) -> object:
            raise RuntimeError("model exploded")

    monkeypatch.setattr(Embedder, "_model", _BrokenModel())
    results = hybrid_search("validate payment", limit=3)

    assert results
    assert results[0]["file_path"] == "app/payments.py"
    assert results[0]["vector_score"] == 0.0
    assert results[0]["keyword_score"] > 0.0
