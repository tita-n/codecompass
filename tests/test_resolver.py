"""Unit tests for cross-file symbol resolution."""

from pathlib import Path

from codecompass.query.resolver import SymbolResolver, module_path_from_file


def test_module_path_from_file() -> None:
    """Test relative path -> dotted module conversion."""
    assert module_path_from_file("app/calculator.py") == "app.calculator"
    assert module_path_from_file("app/__init__.py") == "app"
    assert module_path_from_file("__init__.py") == ""
    assert module_path_from_file("lib.py") == "lib"
    assert module_path_from_file("src/main.py") == "src.main"


def test_resolver_maps_imports(query_db: dict) -> None:
    """Test that from-imports resolve to symbols in the target module."""
    resolver = SymbolResolver(query_db["session"], query_db["repo_root"])
    symbols = query_db["symbols"]

    # from app.auth import validate_token
    assert resolver.resolve_call("app/cli.py", "validate_token") == symbols["validate_token"]
    assert (
        resolver.resolve_call("app/services/login.py", "validate_token")
        == symbols["validate_token"]
    )

    # from app.services.login import login
    assert resolver.resolve_call("app/main.py", "login") == symbols["login"]

    # from app.utils import helper
    assert resolver.resolve_call("app/services/login.py", "helper") == symbols["helper"]


def test_resolver_external_calls_unresolved(query_db: dict) -> None:
    """Test that external / builtin calls stay unresolved."""
    resolver = SymbolResolver(query_db["session"], query_db["repo_root"])

    assert resolver.resolve_call("app/services/login.py", "os.path.join") is None
    assert resolver.resolve_call("app/services/login.py", "len") is None
    assert resolver.resolve_call("app/services/login.py", "validate_token.extra") is None
    assert resolver.resolve_call("app/services/login.py", None) is None
    assert resolver.resolve_call("app/services/login.py", "") is None


def test_resolver_resolves_unresolved_calls(query_db: dict) -> None:
    """Test that unresolved call rows get their callee_id persisted."""
    from codecompass.indexer.schema import Call

    resolver = SymbolResolver(query_db["session"], query_db["repo_root"])
    resolved = resolver.resolve_unresolved_calls()

    assert resolved == 4

    calls = query_db["session"].query(Call).all()
    assert all(call.callee_id is not None for call in calls)


def test_resolver_relative_imports(query_db: dict) -> None:
    """Test relative import resolution (e.g. from ..auth import x)."""
    from datetime import datetime

    from codecompass.indexer.schema import File, Import, Session, Symbol
    from codecompass.query.resolver import SymbolResolver

    session: Session = query_db["session"]
    repo_root = query_db["repo_root"]
    symbols = query_db["symbols"]

    user = File(
        path=f"{repo_root}/app/services/user.py",
        relative_path="app/services/user.py",
        language="python",
        hash="x",
        last_modified=datetime(2026, 1, 1),
        repo_root=repo_root,
    )
    session.add(user)
    session.flush()
    user_sym = Symbol(name="get_user", kind="function", line_start=1, line_end=1, file_id=user.id)
    session.add(user_sym)
    session.flush()
    # from ..auth import validate_token  (module text: "..auth")
    session.add(
        Import(
            file_id=user.id,
            module="..auth",
            imported_name="validate_token",
            is_default=0,
            is_from_import=1,
            line=1,
        )
    )
    session.commit()

    resolver = SymbolResolver(session, repo_root)
    assert (
        resolver.resolve_call("app/services/user.py", "validate_token") == symbols["validate_token"]
    )


def test_resolver_alias_and_wildcard(tmp_path: Path) -> None:
    """Test aliased and wildcard imports."""
    from datetime import datetime

    from codecompass.indexer.schema import File, Import, Symbol, get_session

    session = get_session(str(tmp_path / "alias.db"))
    repo_root = str(tmp_path / "repo")
    ts = datetime(2026, 1, 1)

    lib = File(
        path=str(tmp_path / "repo/lib.py"),
        relative_path="lib.py",
        language="python",
        hash="x",
        last_modified=ts,
        repo_root=repo_root,
    )
    main = File(
        path=str(tmp_path / "repo/main.py"),
        relative_path="main.py",
        language="python",
        hash="x",
        last_modified=ts,
        repo_root=repo_root,
    )
    session.add_all([lib, main])
    session.flush()

    foo = Symbol(name="foo", kind="function", line_start=1, line_end=1, file_id=lib.id)
    bar = Symbol(name="bar", kind="function", line_start=2, line_end=2, file_id=lib.id)
    session.add_all([foo, bar])
    session.flush()

    session.add_all(
        [
            Import(
                file_id=main.id,
                module="lib",
                imported_name="foo",
                alias="f",
                is_from_import=1,
                line=1,
            ),
            Import(file_id=main.id, module="lib", imported_name="*", is_from_import=1, line=2),
        ]
    )
    session.commit()

    from codecompass.query.resolver import SymbolResolver

    resolver = SymbolResolver(session, repo_root)
    assert resolver.resolve_call("main.py", "f") == foo.id
    assert resolver.resolve_call("main.py", "foo") == foo.id
    assert resolver.resolve_call("main.py", "bar") == bar.id
    assert resolver.resolve_call("main.py", "nope") is None
