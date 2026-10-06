"""Shared fixtures for query engine tests."""

import os
import re
import tempfile
import zlib
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
import pytest

from codecompass.indexer.schema import Call, File, Import, Symbol, get_session
from codecompass.llm.base import LLMClient
from codecompass.vector.embedder import Embedder

#: Type variable for the generic fake-LLM installer fixture.
TClient = TypeVar("TClient", bound=LLMClient)

# Keep the real model cache stable across tests that monkeypatch HOME, so the
# slow real-model test downloads `all-MiniLM-L6-v2` at most once per machine.
os.environ.setdefault("HF_HOME", str(Path(tempfile.gettempdir()) / "codecompass-hf-cache"))
os.environ.setdefault(
    "SENTENCE_TRANSFORMERS_HOME",
    str(Path(tempfile.gettempdir()) / "codecompass-st-cache"),
)


class FakeSentenceModel:
    """Deterministic bag-of-words stand-in for SentenceTransformer.

    Tokens are hashed into a fixed-size vector, so identical words share
    features and related queries rank sensibly -- but reproducibly and without
    loading a real model (keeps the suite fast and offline-friendly).
    """

    dim = 384

    def encode(self, texts: list[str], convert_to_numpy: bool = True) -> np.ndarray:
        """Return normalized hash-based vectors for ``texts``."""
        vectors = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in re.findall(r"[A-Za-z0-9_]+", str(text).lower()):
                vectors[row, zlib.crc32(token.encode("utf-8")) % self.dim] += 1.0
            norm = float(np.linalg.norm(vectors[row]))
            if norm > 0:
                vectors[row] /= norm
        return vectors


@pytest.fixture(autouse=True)
def fake_embedder(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the fake embedding model unless a test opts out with @slow."""
    if request.node.get_closest_marker("slow"):
        return
    monkeypatch.setattr(Embedder, "_model", FakeSentenceModel())


class FakeLLMClient(LLMClient):
    """Deterministic in-memory LLM so the suite never touches a real provider.

    Records every call (system/user prompts) so tests can assert on what would
    have been sent, and streams the canned response token by token.
    """

    provider = "fake"
    model = "fake-model"

    def __init__(self, response: str = "Answer with citation [file.py:10-20].") -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        for token in self.response.split(" "):
            yield token + " "

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return self.response


@pytest.fixture
def install_fake_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[TClient], TClient]:
    """Return an installer that routes LLM creation to a fake client."""

    def _install(client: TClient) -> TClient:
        monkeypatch.setattr("codecompass.llm.factory.create_llm_client", lambda config: client)
        return client

    return _install


@pytest.fixture
def query_db(tmp_path: Path) -> dict:
    """Build an indexed sample repo (auth / cli / login / main / tests / circular)."""
    db_path = tmp_path / "query.db"
    repo_root = str(tmp_path / "repo")
    session = get_session(str(db_path))
    timestamp = datetime(2026, 1, 1)

    def add_file(relative_path: str) -> File:
        file_row = File(
            path=str(tmp_path / "repo" / relative_path),
            relative_path=relative_path,
            language="python",
            hash="fixture",
            last_modified=timestamp,
            repo_root=repo_root,
        )
        session.add(file_row)
        session.flush()
        return file_row

    def add_symbol(
        file_row: File,
        name: str,
        kind: str = "function",
        line: int = 1,
        complexity: int | None = None,
        parent_symbol_id: int | None = None,
    ) -> Symbol:
        symbol = Symbol(
            name=name,
            kind=kind,
            signature=None,
            line_start=line,
            line_end=line,
            docstring=None,
            complexity=complexity,
            parent_symbol_id=parent_symbol_id,
            file_id=file_row.id,
        )
        session.add(symbol)
        session.flush()
        return symbol

    def add_import(
        file_row: File,
        module: str,
        imported_name: str | None,
        is_from_import: int = 1,
        alias: str | None = None,
    ) -> None:
        session.add(
            Import(
                file_id=file_row.id,
                module=module,
                imported_name=imported_name,
                alias=alias,
                is_default=0,
                is_from_import=is_from_import,
                line=1,
            )
        )

    # --- files ---
    auth = add_file("app/auth.py")
    cli = add_file("app/cli.py")
    login = add_file("app/services/login.py")
    utils = add_file("app/utils.py")
    main = add_file("app/main.py")
    test = add_file("tests/test_api.py")
    circ_a = add_file("circ/a.py")
    circ_b = add_file("circ/b.py")

    # --- symbols ---
    validate_token = add_symbol(auth, "validate_token", line=5, complexity=4)
    add_symbol(auth, "Token", kind="class", line=10)
    cli_main = add_symbol(cli, "cli_main", line=3, complexity=3)
    login_sym = add_symbol(login, "login", line=8, complexity=6)
    helper = add_symbol(utils, "helper", line=3, complexity=2)
    main_sym = add_symbol(main, "main", line=4, complexity=2)
    add_symbol(test, "test_token", line=4, complexity=1)
    add_symbol(circ_a, "a_func", line=3)
    add_symbol(circ_b, "b_func", line=3)

    # --- imports ---
    add_import(cli, "app.auth", "validate_token")
    add_import(login, "app.auth", "validate_token")
    add_import(login, "app.utils", "helper")
    add_import(login, "os", None, is_from_import=0)  # external
    add_import(main, "app.services.login", "login")
    add_import(circ_a, "circ.b", "b_func")
    add_import(circ_b, "circ.a", "a_func")

    # --- calls (cross-file ones start unresolved so the resolver must fill them) ---
    session.add(Call(caller_id=cli_main.id, callee_id=None, callee_name="validate_token", line=5))
    session.add(Call(caller_id=login_sym.id, callee_id=None, callee_name="validate_token", line=12))
    session.add(Call(caller_id=login_sym.id, callee_id=None, callee_name="helper", line=13))
    session.add(Call(caller_id=main_sym.id, callee_id=None, callee_name="login", line=6))

    # --- test file on disk (needed by find_test_files) ---
    test_dir = tmp_path / "repo" / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_api.py").write_text(
        "from app.auth import validate_token\n\n\n"
        "def test_token():\n"
        "    assert validate_token('abc') is True\n"
    )

    session.commit()

    return {
        "db_path": db_path,
        "repo_root": repo_root,
        "session": session,
        "symbols": {
            "validate_token": validate_token.id,
            "cli_main": cli_main.id,
            "login": login_sym.id,
            "helper": helper.id,
            "main": main_sym.id,
        },
        "files": {
            "auth": "app/auth.py",
            "login": "app/services/login.py",
            "main": "app/main.py",
            "test": "tests/test_api.py",
        },
    }
