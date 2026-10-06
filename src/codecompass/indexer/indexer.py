"""Core indexing logic for CodeCompass."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from tqdm import tqdm
from watchdog.events import FileSystemEvent, FileSystemEventHandler

from codecompass.indexer.chunker import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, chunk_code
from codecompass.indexer.languages import (
    get_language_for_file,
    is_ignored_path,
    is_supported_file,
)
from codecompass.indexer.parser import compute_file_hash, init_parsers, parse_file
from codecompass.indexer.schema import Call as DBCall
from codecompass.indexer.schema import Chunk as DBChunk
from codecompass.indexer.schema import File as DBFile
from codecompass.indexer.schema import Import as DBImport
from codecompass.indexer.schema import IndexingState, get_session, utcnow
from codecompass.indexer.schema import Symbol as DBSymbol
from codecompass.utils.config import get_config


class Indexer:
    """Main indexing engine for CodeCompass."""

    def __init__(self, repo_path: Path, db_path: Path | None = None) -> None:
        self.repo_path = Path(repo_path).absolute()
        self.config = get_config()

        if db_path is None:
            db_path = self.config.get_sqlite_path()

        self.db_path = Path(db_path)
        # Keep vectors alongside the database: with the default paths this is
        # the configured ``~/.codecompass/chroma_db``, while a custom db_path
        # (tests, parallel indexes) gets its own isolated store.
        self.chroma_path = self.db_path.parent / self.config.storage.chroma_path
        self.session = get_session(str(self.db_path))
        self.parsers = init_parsers()

        # Track indexing state
        self.files_indexed = 0
        self.symbols_indexed = 0
        self.calls_indexed = 0
        self.imports_indexed = 0
        self.chunks_indexed = 0

        # File tracking
        self.file_hashes: dict[str, str] = {}
        self.processed_files: set[str] = set()

        # Vector store (created lazily; embedding failures degrade to
        # keyword-only search instead of failing the whole index run)
        self._vector_store: Any = None
        self._vector_store_failed = False
        self._embed_warning_shown = False

    def index(self, force: bool = False, verbose: bool = False) -> dict[str, int]:
        """
        Index the repository.

        Args:
            force: Force re-indexing even if files haven't changed
            verbose: Enable verbose output

        Returns:
            Dict with indexing statistics
        """
        start_time = time.time()

        # Create or reset indexing state for this repository
        state = self._get_indexing_state()
        if state is None:
            state = IndexingState(repo_root=str(self.repo_path), status="indexing")
            self.session.add(state)
        else:
            state.status = "indexing"
            state.error_message = None
        self.session.commit()

        try:
            files_to_index = self._collect_files(verbose=verbose)

            # Drop files that no longer exist on disk (incremental cleanup)
            self._remove_missing_files(files_to_index, verbose=verbose)

            # Snapshot known hashes so unchanged files can be skipped
            if not force:
                self.file_hashes = {
                    f.relative_path: f.hash
                    for f in self.session.query(DBFile).filter_by(repo_root=str(self.repo_path))
                }
            else:
                self.file_hashes = {}

            if verbose:
                print(f"Found {len(files_to_index)} files to index")

            if files_to_index:
                self._process_files(files_to_index, verbose=verbose, force=force)

            if verbose and self.files_indexed == 0:
                print("No files changed since last index")

            # Update state with real totals from the database
            state.status = "complete"
            state.total_files = self._count_files()
            state.total_symbols = self._count_symbols()
            state.total_calls = self._count_calls()
            state.total_imports = self._count_imports()
            state.duration_seconds = int(time.time() - start_time)
            state.last_indexed_at = utcnow()
            self.session.commit()
        except Exception as exc:
            state.status = "error"
            state.error_message = str(exc)
            self.session.commit()
            raise

        return {
            "files": self.files_indexed,
            "symbols": self.symbols_indexed,
            "calls": self.calls_indexed,
            "imports": self.imports_indexed,
            "chunks": self.chunks_indexed,
            "duration_seconds": state.duration_seconds,
        }

    # ------------------------------------------------------------------
    # File collection
    # ------------------------------------------------------------------

    def _collect_files(self, verbose: bool = False) -> list[Path]:
        """Collect all files to index."""
        files: list[Path] = []
        max_size_bytes = self.config.indexing.max_file_size_mb * 1024 * 1024

        for root, dirs, filenames in os.walk(self.repo_path):
            # Filter directories in place so os.walk prunes ignored subtrees
            dirs[:] = sorted(d for d in dirs if not is_ignored_path(Path(root) / d, self.repo_path))

            for filename in filenames:
                file_path = Path(root) / filename

                if not is_supported_file(file_path):
                    continue

                if is_ignored_path(file_path, self.repo_path):
                    continue

                try:
                    if file_path.stat().st_size > max_size_bytes:
                        if verbose:
                            print(f"Skipping oversized file: {file_path}")
                        continue
                except OSError:
                    continue

                files.append(file_path)

        return files

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _process_files(self, files: list[Path], verbose: bool = False, force: bool = False) -> None:
        """Parse files in parallel, then store results sequentially.

        Parsing is CPU-bound inside tree-sitter (which releases the GIL), while
        database writes happen only on the main thread to keep the shared
        session safe.
        """
        with ThreadPoolExecutor(max_workers=self.config.performance.max_workers) as executor:
            futures = {
                executor.submit(self._process_single_file, file_path, force): file_path
                for file_path in files
            }

            with tqdm(total=len(files), desc="Indexing", unit="files") as pbar:
                for future in as_completed(futures):
                    file_path = futures[future]
                    try:
                        result = future.result()
                        if result is not None:
                            counts = self._store_file_data(**result)
                            self.files_indexed += 1
                            self.symbols_indexed += counts["symbols"]
                            self.calls_indexed += counts["calls"]
                            self.imports_indexed += counts["imports"]

                        pbar.update(1)
                        pbar.set_postfix(
                            {"symbols": self.symbols_indexed, "calls": self.calls_indexed}
                        )
                    except Exception as exc:
                        if verbose:
                            print(f"Error processing {file_path}: {exc}")
                        pbar.update(1)

    def _process_single_file(self, file_path: Path, force: bool = False) -> dict[str, Any] | None:
        """
        Parse a single file (hash check + tree-sitter extraction).

        Returns:
            Dict with file metadata and extracted symbols/calls/imports, or
            ``None`` if the file is unchanged or unsupported.
        """
        file_hash = compute_file_hash(file_path)
        relative_path = file_path.relative_to(self.repo_path)
        rel_str = relative_path.as_posix()

        # Skip unchanged files (incremental indexing)
        if not force and self.file_hashes.get(rel_str) == file_hash:
            return None

        lang_config = get_language_for_file(file_path)
        if lang_config is None:
            return None

        parser = self.parsers.get(lang_config.name)
        if parser is None:
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            symbols, calls, imports = parse_file(file_path, content, parser, lang_config.name)

            # Chunk the file for embedding/retrieval (cheap; runs in the
            # parse worker pool, while embedding happens on the main thread).
            performance = self.config.performance
            chunks = chunk_code(
                content,
                rel_str,
                lang_config.name,
                symbols,
                chunk_size=performance.chunk_size or DEFAULT_CHUNK_SIZE,
                overlap=performance.chunk_overlap or DEFAULT_CHUNK_OVERLAP,
            )

            return {
                "file_path": file_path,
                "relative_path": relative_path,
                "language": lang_config.name,
                "file_hash": file_hash,
                "symbols": symbols,
                "calls": calls,
                "imports": imports,
                "chunks": chunks,
            }
        except Exception as exc:
            print(f"Error parsing {file_path}: {exc}")
            return None

    def _store_file_data(
        self,
        file_path: Path,
        relative_path: Path,
        language: str,
        file_hash: str,
        symbols: list[dict],
        calls: list[dict],
        imports: list[dict],
        chunks: list[dict] | None = None,
    ) -> dict[str, int]:
        """Store extracted data for one file in the database.

        Replaces any previously indexed data for the same file. Call
        relationships are resolved against symbols in the same file (cross-file
        resolution arrives with the query engine in Phase 2). Chunks are
        persisted for FTS keyword search and embedded into ChromaDB for vector
        search.
        """
        rel_str = relative_path.as_posix()
        repo_root = str(self.repo_path)

        existing = (
            self.session.query(DBFile).filter_by(repo_root=repo_root, relative_path=rel_str).first()
        )
        if existing is not None:
            self._delete_file_data(existing)

        db_file = DBFile(
            path=str(file_path.absolute()),
            relative_path=rel_str,
            language=language,
            hash=file_hash,
            last_modified=datetime.fromtimestamp(file_path.stat().st_mtime),
            repo_root=repo_root,
        )
        self.session.add(db_file)
        self.session.flush()

        # Insert symbols
        symbol_rows: list[DBSymbol] = []
        for symbol in symbols:
            symbol_rows.append(
                DBSymbol(
                    name=str(symbol.get("name", "")),
                    kind=str(symbol.get("kind", "unknown")),
                    signature=symbol.get("signature"),
                    line_start=int(symbol.get("line_start", 0)),
                    line_end=int(symbol.get("line_end", 0)),
                    docstring=symbol.get("docstring"),
                    complexity=symbol.get("complexity"),
                    file_id=db_file.id,
                )
            )
        self.session.add_all(symbol_rows)
        self.session.flush()

        by_name: dict[str, list[DBSymbol]] = {}
        for row in symbol_rows:
            by_name.setdefault(row.name, []).append(row)

        # Resolve parent symbols (methods -> classes, nested functions, etc.)
        for row, symbol in zip(symbol_rows, symbols, strict=True):
            parent_name = symbol.get("parent")
            if parent_name:
                parent = self._resolve_symbol(by_name, str(parent_name), row.line_start)
                if parent is not None and parent.id != row.id:
                    row.parent_symbol_id = parent.id

        # Insert calls. Intra-file callees are resolved here; everything else is
        # stored with a NULL callee_id (plus the extracted callee name) so the
        # query engine's resolver can attempt cross-file resolution later.
        inserted_calls = 0
        for call in calls:
            line = int(call.get("line", 0))
            caller = self._resolve_symbol(by_name, str(call.get("caller", "")), line)
            if caller is None:
                continue
            # Intra-file resolution uses the leaf name (e.g. "self.add" -> "add").
            callee_name = call.get("callee")
            leaf = str(callee_name).split(".")[-1] if callee_name else ""
            callee = self._resolve_symbol(by_name, leaf, line)
            self.session.add(
                DBCall(
                    caller_id=caller.id,
                    callee_id=callee.id if callee is not None else None,
                    callee_name=callee_name,
                    line=line,
                    call_text=call.get("call_text"),
                )
            )
            inserted_calls += 1

        # Insert imports
        for imp in imports:
            self.session.add(
                DBImport(
                    file_id=db_file.id,
                    module=str(imp.get("module", "")),
                    imported_name=imp.get("imported_name"),
                    alias=imp.get("alias"),
                    is_default=int(imp.get("is_default", 0)),
                    is_from_import=int(imp.get("is_from_import", 0)),
                    line=imp.get("line"),
                )
            )

        # Insert chunks (for FTS keyword search), then embed them into the
        # vector store. Embedding failures degrade to keyword-only search.
        stored_chunks = self._store_chunks(db_file, rel_str, language, chunks or [], by_name)

        self.processed_files.add(rel_str)
        self.session.commit()

        return {
            "symbols": len(symbol_rows),
            "calls": inserted_calls,
            "imports": len(imports),
            "chunks": stored_chunks,
        }

    def _store_chunks(
        self,
        db_file: DBFile,
        rel_str: str,
        language: str,
        chunks: list[dict],
        by_name: dict[str, list[DBSymbol]],
    ) -> int:
        """Persist chunks for a file and push their embeddings to ChromaDB.

        Returns the number of chunks stored.
        """
        if not chunks:
            return 0

        chunk_rows: list[DBChunk] = []
        for chunk in chunks:
            symbol_id = None
            symbol_name = chunk.get("symbol_name")
            if symbol_name:
                symbol = self._resolve_symbol(
                    by_name, str(symbol_name), int(chunk.get("line_start", 0))
                )
                symbol_id = symbol.id if symbol is not None else None
            chunk_rows.append(
                DBChunk(
                    file_id=db_file.id,
                    symbol_id=symbol_id,
                    file_path=rel_str,
                    symbol_name=symbol_name,
                    content=str(chunk.get("content", "")),
                    language=language,
                    line_start=int(chunk.get("line_start", 0)),
                    line_end=int(chunk.get("line_end", 0)),
                    chunk_index=int(chunk.get("chunk_index", 0)),
                )
            )
        self.session.add_all(chunk_rows)
        self.session.flush()
        self.chunks_indexed += len(chunk_rows)

        self._embed_chunks(chunk_rows)
        return len(chunk_rows)

    def _embed_chunks(self, chunk_rows: list[DBChunk]) -> None:
        """Generate embeddings for chunk rows and upsert them into ChromaDB."""
        from codecompass.vector.embedder import Embedder

        try:
            embeddings = Embedder.embed([row.content or "" for row in chunk_rows])
        except Exception as exc:
            if not self._embed_warning_shown:
                self._embed_warning_shown = True
                print(
                    f"Warning: embeddings unavailable ({exc}); "
                    "queries will use keyword search only"
                )
            return

        store = self._get_vector_store()
        if store is None:
            return

        repo_root = str(self.repo_path)
        payload = [
            {
                "id": row.id,
                "chunk_id": row.id,
                "file_path": row.file_path,
                "symbol_name": row.symbol_name,
                "line_start": row.line_start,
                "line_end": row.line_end,
                "language": row.language,
                "chunk_index": row.chunk_index,
                "content": row.content,
                "repo_root": repo_root,
            }
            for row in chunk_rows
        ]
        try:
            ids = store.add_chunks(payload, embeddings)
        except Exception as exc:
            if not self._embed_warning_shown:
                self._embed_warning_shown = True
                print(
                    f"Warning: vector store unavailable ({exc}); "
                    "queries will use keyword search only"
                )
            return

        for row, embedding_id in zip(chunk_rows, ids, strict=True):
            row.embedding_id = embedding_id

    def _get_vector_store(self) -> Any:
        """Return the shared VectorStore, creating it once (or None on failure)."""
        if self._vector_store is None and not self._vector_store_failed:
            try:
                from codecompass.vector.store import VectorStore

                self._vector_store = VectorStore(self.chroma_path)
            except Exception as exc:
                self._vector_store_failed = True
                print(f"Warning: could not open vector store: {exc}")
        return self._vector_store

    @staticmethod
    def _resolve_symbol(
        by_name: dict[str, list[DBSymbol]], name: str, line: int
    ) -> DBSymbol | None:
        """Resolve a symbol name to a DB row, preferring range containment."""
        candidates = by_name.get(name, [])
        if not candidates:
            return None

        # Prefer the smallest symbol whose line range contains the reference.
        contained = [c for c in candidates if c.line_start <= line <= c.line_end]
        if contained:
            return min(contained, key=lambda c: c.line_end - c.line_start)

        # Otherwise prefer a top-level symbol, then any single match.
        top_level = [c for c in candidates if c.parent_symbol_id is None]
        if top_level:
            return top_level[0]
        return candidates[0]

    def _delete_file_data(self, db_file: DBFile) -> None:
        """Remove a file and all of its symbols, calls, imports and chunks."""
        # Drop chunks first (SQLite rows + FTS index via triggers, then the
        # mirrored vectors in ChromaDB) while the file row still exists.
        self.session.query(DBChunk).filter(DBChunk.file_id == db_file.id).delete(
            synchronize_session=False
        )
        store = self._get_vector_store()
        if store is not None:
            try:
                store.delete_by_file(db_file.relative_path, db_file.repo_root)
            except Exception as exc:
                print(f"Warning: could not clean vector store for {db_file.relative_path}: {exc}")

        symbol_ids = [
            row[0] for row in self.session.query(DBSymbol.id).filter_by(file_id=db_file.id)
        ]
        if symbol_ids:
            self.session.query(DBCall).filter(
                or_(DBCall.caller_id.in_(symbol_ids), DBCall.callee_id.in_(symbol_ids))
            ).delete(synchronize_session=False)
            self.session.query(DBSymbol).filter(DBSymbol.id.in_(symbol_ids)).delete(
                synchronize_session=False
            )
        self.session.query(DBImport).filter(DBImport.file_id == db_file.id).delete(
            synchronize_session=False
        )
        self.session.delete(db_file)
        self.session.flush()

    def _remove_missing_files(self, files: list[Path], verbose: bool = False) -> int:
        """Remove index entries for files that no longer exist on disk."""
        current = {f.relative_to(self.repo_path).as_posix() for f in files}
        removed = 0
        db_files = self.session.query(DBFile).filter_by(repo_root=str(self.repo_path)).all()
        for db_file in db_files:
            if db_file.relative_path not in current:
                self._delete_file_data(db_file)
                removed += 1
        if removed:
            self.session.commit()
            if verbose:
                print(f"Removed {removed} file(s) deleted from the repository")
        return removed

    # ------------------------------------------------------------------
    # Incremental (watchdog) support
    # ------------------------------------------------------------------

    def reindex_file(self, file_path: Path) -> dict[str, int] | None:
        """Re-index a single file (used by the file watcher)."""
        try:
            result = self._process_single_file(file_path, force=True)
        except Exception as exc:
            print(f"Error re-indexing {file_path}: {exc}")
            return None
        if result is None:
            return None
        return self._store_file_data(**result)

    def remove_file(self, file_path: Path) -> None:
        """Remove a file from the index (used by the file watcher)."""
        try:
            relative = file_path.absolute().relative_to(self.repo_path)
        except ValueError:
            return
        db_file = (
            self.session.query(DBFile)
            .filter_by(repo_root=str(self.repo_path), relative_path=relative.as_posix())
            .first()
        )
        if db_file is not None:
            self._delete_file_data(db_file)
            self.session.commit()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _get_indexing_state(self) -> IndexingState | None:
        """Get the current indexing state for the repository."""
        return self.session.query(IndexingState).filter_by(repo_root=str(self.repo_path)).first()

    def _count_files(self) -> int:
        return self.session.query(DBFile).filter_by(repo_root=str(self.repo_path)).count()

    def _count_symbols(self) -> int:
        return (
            self.session.query(DBSymbol)
            .join(DBFile)
            .filter(DBFile.repo_root == str(self.repo_path))
            .count()
        )

    def _count_calls(self) -> int:
        return (
            self.session.query(DBCall)
            .join(DBSymbol, DBCall.caller_id == DBSymbol.id)
            .join(DBFile)
            .filter(DBFile.repo_root == str(self.repo_path))
            .count()
        )

    def _count_imports(self) -> int:
        return (
            self.session.query(DBImport)
            .join(DBFile)
            .filter(DBFile.repo_root == str(self.repo_path))
            .count()
        )


class FileChangeHandler(FileSystemEventHandler):
    """Handle file system changes for incremental indexing."""

    def __init__(self, indexer: Indexer) -> None:
        self.indexer = indexer

    @staticmethod
    def _event_path(event: FileSystemEvent) -> Path:
        """Convert a watchdog event path (str or bytes) to a Path."""
        return Path(os.fsdecode(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_supported_file(self._event_path(event)):
            self.indexer.reindex_file(self._event_path(event))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_supported_file(self._event_path(event)):
            self.indexer.reindex_file(self._event_path(event))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.indexer.remove_file(self._event_path(event))
