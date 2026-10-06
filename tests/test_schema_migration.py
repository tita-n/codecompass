"""Tests for schema migrations applied to databases from earlier versions."""

import sqlite3
from pathlib import Path

from sqlalchemy import text

from codecompass.indexer.schema import Call, get_session

_OLD_SCHEMA = """
CREATE TABLE files (
    id INTEGER NOT NULL PRIMARY KEY,
    path VARCHAR(1024) NOT NULL,
    relative_path VARCHAR(1024) NOT NULL,
    language VARCHAR(50) NOT NULL,
    hash VARCHAR(64) NOT NULL,
    last_modified DATETIME NOT NULL,
    indexed_at DATETIME NOT NULL,
    repo_root VARCHAR(1024) NOT NULL
);
CREATE TABLE symbols (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(50) NOT NULL,
    signature TEXT,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    docstring TEXT,
    parent_symbol_id INTEGER,
    file_id INTEGER NOT NULL
);
CREATE TABLE calls (
    id INTEGER NOT NULL PRIMARY KEY,
    caller_id INTEGER NOT NULL,
    callee_id INTEGER NOT NULL,
    line INTEGER,
    call_text TEXT
);
CREATE TABLE imports (
    id INTEGER NOT NULL PRIMARY KEY,
    file_id INTEGER NOT NULL,
    module VARCHAR(1024) NOT NULL,
    imported_name VARCHAR(255),
    alias VARCHAR(255),
    is_default INTEGER NOT NULL,
    is_from_import INTEGER NOT NULL,
    line INTEGER
);
CREATE TABLE indexing_state (
    id INTEGER NOT NULL PRIMARY KEY,
    repo_root VARCHAR(1024) NOT NULL,
    last_indexed_at DATETIME NOT NULL,
    total_files INTEGER NOT NULL,
    total_symbols INTEGER NOT NULL,
    total_calls INTEGER NOT NULL,
    total_imports INTEGER NOT NULL,
    duration_seconds INTEGER,
    status VARCHAR(50) NOT NULL,
    error_message TEXT
);
"""


def _build_old_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    # Seed one file, symbol, and call (old schema has NOT NULL callee_id)
    conn.execute(
        "INSERT INTO files (id, path, relative_path, language, hash, last_modified, indexed_at, repo_root) "
        "VALUES (1, '/repo/a.py', 'a.py', 'python', 'abc', '2026-01-01', '2026-01-01', '/repo')"
    )
    conn.execute(
        "INSERT INTO symbols (id, name, kind, line_start, line_end, file_id) "
        "VALUES (1, 'foo', 'function', 1, 2, 1)"
    )
    conn.execute(
        "INSERT INTO calls (id, caller_id, callee_id, line, call_text) "
        "VALUES (1, 1, 1, 3, 'foo()')"
    )
    conn.commit()
    conn.close()


def test_migration_upgrades_old_database(tmp_path: Path) -> None:
    """Test that an old-schema DB gains new columns and a nullable callee_id."""
    db_path = tmp_path / "old.db"
    _build_old_db(db_path)

    session = get_session(str(db_path))

    # symbols gained a complexity column
    columns = session.execute(text("PRAGMA table_info(symbols)")).fetchall()
    column_names = {row[1] for row in columns}
    assert "complexity" in column_names

    # calls gained callee_name and a nullable callee_id
    call_columns = session.execute(text("PRAGMA table_info(calls)")).fetchall()
    call_cols = {row[1]: row[3] for row in call_columns}  # name -> notnull
    assert "callee_name" in call_cols
    assert call_cols["callee_id"] == 0  # no longer NOT NULL

    # existing rows survived the table rebuild
    calls = session.query(Call).all()
    assert len(calls) == 1
    assert calls[0].caller_id == 1
    assert calls[0].callee_id == 1

    # NULL callee_id inserts now work (needed for unresolved cross-file calls)
    from codecompass.indexer.schema import Symbol

    session.add(Symbol(name="bar", kind="function", line_start=1, line_end=1, file_id=1))
    session.flush()
    session.add(Call(caller_id=1, callee_id=None, callee_name="external", line=5))
    session.commit()
    assert session.query(Call).filter(Call.callee_id.is_(None)).count() == 1


def test_fresh_database_needs_no_rebuild(tmp_path: Path) -> None:
    """Test that a brand-new database is created with the current schema."""
    db_path = tmp_path / "fresh.db"
    session = get_session(str(db_path))

    call_columns = session.execute(text("PRAGMA table_info(calls)")).fetchall()
    call_cols = {row[1]: row[3] for row in call_columns}
    assert call_cols["callee_id"] == 0
    assert "callee_name" in call_cols

    symbol_columns = session.execute(text("PRAGMA table_info(symbols)")).fetchall()
    assert "complexity" in {row[1] for row in symbol_columns}
