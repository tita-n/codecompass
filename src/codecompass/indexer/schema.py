"""SQLAlchemy database schema for CodeCompass indexer."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (suitable for SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class File(Base):
    """Represents a source file in the repository."""

    __tablename__ = "files"

    id: Mapped[int] = Column(Integer, primary_key=True)
    path: Mapped[str] = Column(String(1024), nullable=False, unique=True)
    relative_path: Mapped[str] = Column(String(1024), nullable=False)
    language: Mapped[str] = Column(String(50), nullable=False)
    hash: Mapped[str] = Column(String(64), nullable=False)  # SHA256 of file content
    last_modified: Mapped[datetime] = Column(DateTime, nullable=False)
    indexed_at: Mapped[datetime] = Column(DateTime, nullable=False, default=utcnow)
    repo_root: Mapped[str] = Column(String(1024), nullable=False)

    # Relationships
    symbols: Mapped[list["Symbol"]] = relationship(
        "Symbol", back_populates="file", cascade="all, delete-orphan"
    )
    imports: Mapped[list["Import"]] = relationship(
        "Import", back_populates="file", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_files_path", "path"),
        Index("idx_files_repo_root", "repo_root"),
        Index("idx_files_relative_path", "relative_path"),
    )

    def __repr__(self) -> str:
        return f"<File(path='{self.relative_path}', language='{self.language}')>"


class Symbol(Base):
    """Represents a symbol (function, class, method, etc.) in the codebase."""

    __tablename__ = "symbols"

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(255), nullable=False, index=True)
    kind: Mapped[str] = Column(
        String(50), nullable=False
    )  # function, class, method, interface, enum, etc.
    signature = Column(Text, nullable=True)
    line_start: Mapped[int] = Column(Integer, nullable=False)
    line_end: Mapped[int] = Column(Integer, nullable=False)
    docstring = Column(Text, nullable=True)
    complexity = Column(Integer, nullable=True)  # cyclomatic complexity
    parent_symbol_id = Column(Integer, ForeignKey("symbols.id"), nullable=True)
    file_id: Mapped[int] = Column(Integer, ForeignKey("files.id"), nullable=False, index=True)

    # Relationships
    file: Mapped["File"] = relationship("File", back_populates="symbols")
    parent: Mapped["Symbol | None"] = relationship("Symbol", remote_side=[id], backref="children")
    outgoing_calls: Mapped[list["Call"]] = relationship(
        "Call", foreign_keys="Call.caller_id", back_populates="caller"
    )
    incoming_calls: Mapped[list["Call"]] = relationship(
        "Call", foreign_keys="Call.callee_id", back_populates="callee"
    )

    __table_args__ = (
        Index("idx_symbols_name_kind", "name", "kind"),
        Index("idx_symbols_file_id_line_start", "file_id", "line_start"),
    )

    def __repr__(self) -> str:
        return f"<Symbol(name='{self.name}', kind='{self.kind}', file_id={self.file_id})>"


class Call(Base):
    """Represents a call relationship between symbols."""

    __tablename__ = "calls"

    id: Mapped[int] = Column(Integer, primary_key=True)
    caller_id: Mapped[int] = Column(Integer, ForeignKey("symbols.id"), nullable=False, index=True)
    callee_id = Column(Integer, ForeignKey("symbols.id"), nullable=True, index=True)
    callee_name = Column(
        String(255), nullable=True
    )  # extracted callee name, e.g. "validate_token" or "os.path.join"
    line = Column(Integer, nullable=True)
    call_text = Column(Text, nullable=True)  # e.g., "validatePayment(amount)"

    # Relationships
    caller: Mapped["Symbol"] = relationship(
        "Symbol", foreign_keys=[caller_id], back_populates="outgoing_calls"
    )
    callee: Mapped["Symbol | None"] = relationship(
        "Symbol", foreign_keys=[callee_id], back_populates="incoming_calls"
    )

    __table_args__ = (
        Index("idx_calls_caller_callee", "caller_id", "callee_id"),
        Index("idx_calls_callee_id", "callee_id"),
    )

    def __repr__(self) -> str:
        return f"<Call(caller_id={self.caller_id}, callee_id={self.callee_id})>"


class Import(Base):
    """Represents an import statement."""

    __tablename__ = "imports"

    id: Mapped[int] = Column(Integer, primary_key=True)
    file_id: Mapped[int] = Column(Integer, ForeignKey("files.id"), nullable=False, index=True)
    module: Mapped[str] = Column(String(1024), nullable=False)  # e.g., "os", "path/to/module"
    imported_name = Column(String(255), nullable=True)  # e.g., "validatePayment"
    alias = Column(String(255), nullable=True)  # e.g., "vp" for "import validatePayment as vp"
    is_default: Mapped[int] = Column(
        Integer, nullable=False, default=0
    )  # SQLite booleans are integers
    is_from_import: Mapped[int] = Column(Integer, nullable=False, default=0)
    line = Column(Integer, nullable=True)

    # Relationships
    file: Mapped["File"] = relationship("File", back_populates="imports")

    __table_args__ = (
        Index("idx_imports_file_id", "file_id"),
        Index("idx_imports_module", "module"),
        Index("idx_imports_imported_name", "imported_name"),
    )

    def __repr__(self) -> str:
        return f"<Import(module='{self.module}', imported='{self.imported_name}')>"


class Chunk(Base):
    """Represents a chunk of source code prepared for embedding and retrieval."""

    __tablename__ = "chunks"

    id: Mapped[int] = Column(Integer, primary_key=True)
    file_id: Mapped[int] = Column(Integer, ForeignKey("files.id"), nullable=False, index=True)
    symbol_id = Column(Integer, ForeignKey("symbols.id"), nullable=True, index=True)
    # Repo-relative path and symbol name are denormalized so the FTS index and
    # search results can be served without joins.
    file_path: Mapped[str] = Column(String(1024), nullable=False)
    symbol_name = Column(String(255), nullable=True)
    content: Mapped[str] = Column(Text, nullable=False)
    language: Mapped[str] = Column(String(50), nullable=False)
    line_start: Mapped[int] = Column(Integer, nullable=False)
    line_end: Mapped[int] = Column(Integer, nullable=False)
    chunk_index: Mapped[int] = Column(Integer, nullable=False)
    embedding_id = Column(String(255), nullable=True)  # ChromaDB ID

    # Relationships
    file: Mapped["File"] = relationship("File")
    symbol: Mapped["Symbol | None"] = relationship("Symbol")

    __table_args__ = (
        Index("idx_chunks_file_id_chunk_index", "file_id", "chunk_index"),
        Index("idx_chunks_file_path", "file_path"),
    )

    def __repr__(self) -> str:
        return (
            f"<Chunk(file_path='{self.file_path}', lines={self.line_start}-{self.line_end}, "
            f"index={self.chunk_index})>"
        )


class IndexingState(Base):
    """Tracks the state of indexing for incremental updates."""

    __tablename__ = "indexing_state"

    id: Mapped[int] = Column(Integer, primary_key=True)
    repo_root: Mapped[str] = Column(String(1024), nullable=False, unique=True, index=True)
    last_indexed_at: Mapped[datetime] = Column(DateTime, nullable=False, default=utcnow)
    total_files: Mapped[int] = Column(Integer, nullable=False, default=0)
    total_symbols: Mapped[int] = Column(Integer, nullable=False, default=0)
    total_calls: Mapped[int] = Column(Integer, nullable=False, default=0)
    total_imports: Mapped[int] = Column(Integer, nullable=False, default=0)
    duration_seconds = Column(Integer, nullable=True)
    status: Mapped[str] = Column(
        String(50), nullable=False, default="pending"
    )  # pending, indexing, complete, error
    error_message = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<IndexingState(repo='{self.repo_root}', status='{self.status}')>"


# ---------------------------------------------------------------------------
# Lightweight migrations for databases created by earlier versions
# ---------------------------------------------------------------------------

# DDL for the current ``calls`` table shape (used to rebuild old tables whose
# ``callee_id`` was NOT NULL before cross-file resolution existed).
_CALLS_TABLE_DDL = """
CREATE TABLE calls_new (
    id INTEGER NOT NULL PRIMARY KEY,
    caller_id INTEGER NOT NULL,
    callee_id INTEGER,
    callee_name VARCHAR(255),
    line INTEGER,
    call_text TEXT,
    FOREIGN KEY(caller_id) REFERENCES symbols (id),
    FOREIGN KEY(callee_id) REFERENCES symbols (id)
)
"""

# Full-text search over the ``chunks`` table. The FTS5 table is an
# external-content index: rows are read from ``chunks`` and kept in sync by
# triggers, so ORM inserts/updates/deletes need no extra bookkeeping.
_CHUNKS_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, file_path, symbol_name, language,
    content='chunks', content_rowid='id'
)
"""

_CHUNKS_FTS_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, content, file_path, symbol_name, language)
        VALUES (new.id, new.content, new.file_path, new.symbol_name, new.language);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, content, file_path, symbol_name, language)
        VALUES ('delete', old.id, old.content, old.file_path, old.symbol_name, old.language);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, content, file_path, symbol_name, language)
        VALUES ('delete', old.id, old.content, old.file_path, old.symbol_name, old.language);
        INSERT INTO chunks_fts(rowid, content, file_path, symbol_name, language)
        VALUES (new.id, new.content, new.file_path, new.symbol_name, new.language);
    END
    """,
)


def _table_columns(connection: Any, table: str) -> dict[str, int]:
    """Return ``{column_name: notnull}`` for a table."""
    result = connection.exec_driver_sql(f"PRAGMA table_info({table})")
    return {row[1]: int(row[3]) for row in result}


def _ensure_chunks_fts(connection: Any) -> None:
    """Create the ``chunks`` FTS5 index (plus sync triggers) if missing.

    Backfills the index from existing rows the first time it is created so
    databases from earlier versions gain full-text search over their chunks.
    """
    existed = (
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        is not None
    )
    connection.exec_driver_sql(_CHUNKS_FTS_DDL)
    for trigger in _CHUNKS_FTS_TRIGGERS:
        connection.exec_driver_sql(trigger)
    if not existed:
        connection.exec_driver_sql(
            "INSERT INTO chunks_fts(rowid, content, file_path, symbol_name, language) "
            "SELECT id, content, file_path, symbol_name, language FROM chunks"
        )


def _migrate(engine: Engine) -> None:
    """Apply schema migrations for databases created by earlier versions."""
    with engine.begin() as connection:
        symbol_cols = _table_columns(connection, "symbols")
        if symbol_cols and "complexity" not in symbol_cols:
            connection.exec_driver_sql("ALTER TABLE symbols ADD COLUMN complexity INTEGER")

        _ensure_chunks_fts(connection)

        call_cols = _table_columns(connection, "calls")
        if not call_cols:
            return
        if "callee_name" not in call_cols:
            connection.exec_driver_sql("ALTER TABLE calls ADD COLUMN callee_name VARCHAR(255)")

        # Old tables had NOT NULL on callee_id; SQLite can only drop that
        # constraint by rebuilding the table.
        if call_cols["callee_id"]:
            _rebuild_calls(connection, set(call_cols))


def _rebuild_calls(connection: Any, old_columns: set[str]) -> None:
    """Rebuild the ``calls`` table with the current (nullable) schema."""
    connection.exec_driver_sql(_CALLS_TABLE_DDL)
    columns = [
        c
        for c in ("id", "caller_id", "callee_id", "callee_name", "line", "call_text")
        if c in old_columns
    ]
    column_list = ", ".join(columns)
    connection.exec_driver_sql(
        f"INSERT INTO calls_new ({column_list}) SELECT {column_list} FROM calls"
    )
    connection.exec_driver_sql("DROP TABLE calls")
    connection.exec_driver_sql("ALTER TABLE calls_new RENAME TO calls")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_calls_caller_callee ON calls (caller_id, callee_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_calls_callee_id ON calls (callee_id)"
    )


# Database setup
def get_engine(db_path: str | Path) -> Engine:
    """Create SQLAlchemy engine for the given database path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


def init_db(db_path: str | Path) -> Session:
    """Initialize the database schema and return a session."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return sessionmaker(bind=engine)()


def get_session(db_path: str | Path) -> Session:
    """Get a database session, ensuring the schema exists and is migrated."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return sessionmaker(bind=engine)()
