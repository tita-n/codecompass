"""Tests for the NetworkX-backed call graph."""

from codecompass.query.graph import CodeGraph


def _graph(query_db: dict) -> CodeGraph:
    return CodeGraph(query_db["session"], query_db["repo_root"])


def test_graph_resolves_cross_file_calls(query_db: dict) -> None:
    """Test that cross-file calls become graph edges."""
    graph = _graph(query_db)
    symbols = query_db["symbols"]

    assert graph.graph.number_of_nodes() == 9
    assert (symbols["main"], symbols["login"]) in graph.graph.edges()
    assert (symbols["login"], symbols["validate_token"]) in graph.graph.edges()
    assert (symbols["cli_main"], symbols["validate_token"]) in graph.graph.edges()
    assert (symbols["login"], symbols["helper"]) in graph.graph.edges()


def test_get_callers(query_db: dict) -> None:
    """Test cross-file caller lookup."""
    graph = _graph(query_db)
    callers = graph.get_callers("validate_token")

    names = {c["name"] for c in callers}
    assert names == {"login", "cli_main"}
    for caller in callers:
        assert caller["file"].startswith("app/")
        assert caller["line"] >= 1


def test_get_callees(query_db: dict) -> None:
    """Test callee lookup."""
    graph = _graph(query_db)
    callees = graph.get_callees("login")

    names = {c["name"] for c in callees}
    assert names == {"validate_token", "helper"}
    assert all(c["file"] for c in callees)


def test_get_dependency_tree(query_db: dict) -> None:
    """Test nested dependency tree construction."""
    graph = _graph(query_db)
    roots = graph.get_dependency_tree("main", depth=2)

    assert len(roots) == 1
    root = roots[0]
    assert root["name"] == "main"

    level1 = {c["name"] for c in root["children"]}
    assert level1 == {"login"}

    login_node = next(c for c in root["children"] if c["name"] == "login")
    level2 = {c["name"] for c in login_node["children"]}
    assert level2 == {"validate_token", "helper"}


def test_get_dependency_tree_handles_cycles(query_db: dict) -> None:
    """Test that the tree builder terminates on cyclic graphs."""
    graph = _graph(query_db)
    # login calls itself indirectly is absent, but a self-loop is easy to add.
    symbols = query_db["symbols"]
    graph.graph.add_edge(symbols["login"], symbols["login"])
    roots = graph.get_dependency_tree("login", depth=5)
    assert roots[0]["name"] == "login"
    # Self-loop should appear at most once per level
    names = [c["name"] for c in roots[0]["children"]]
    assert names.count("login") <= 2


def test_find_path(query_db: dict) -> None:
    """Test shortest path between symbols."""
    graph = _graph(query_db)
    path = graph.find_path("main", "validate_token")

    assert path is not None
    assert [p["name"] for p in path] == ["main", "login", "validate_token"]


def test_find_path_no_path(query_db: dict) -> None:
    """Test that a missing path returns None."""
    graph = _graph(query_db)
    assert graph.find_path("validate_token", "main") is None
    assert graph.find_path("does_not_exist", "main") is None


def test_get_impact(query_db: dict) -> None:
    """Test impact (blast radius) analysis."""
    graph = _graph(query_db)
    result = graph.get_impact("validate_token")

    assert result is not None
    assert result["symbol"] == "validate_token"
    assert result["risk"] == "MEDIUM"  # 2 direct callers
    assert {c["name"] for c in result["direct_callers"]} == {"login", "cli_main"}
    # main -> login -> validate_token, so main is a transitive caller (depth 3)
    assert result["total_affected"] == 3
    assert "tests/test_api.py" in result["test_files"]


def test_get_impact_unknown_symbol(query_db: dict) -> None:
    """Test impact on a symbol that does not exist."""
    graph = _graph(query_db)
    assert graph.get_impact("missing") is None


def test_find_test_files(query_db: dict) -> None:
    """Test test file discovery."""
    graph = _graph(query_db)
    assert graph.find_test_files("validate_token") == ["tests/test_api.py"]
    assert graph.find_test_files("Token") == []  # not referenced in tests


def test_module_graph(query_db: dict) -> None:
    """Test the module-level import graph."""
    graph = _graph(query_db)
    edges = set(graph.module_graph.edges())
    assert ("app/main.py", "app/services/login.py") in edges
    assert ("app/services/login.py", "app/auth.py") in edges
    assert ("circ/a.py", "circ/b.py") in edges
    assert ("circ/b.py", "circ/a.py") in edges
    # external imports produce no module edge
    assert not any("os" in (u, v) for u, v in edges)


def test_file_import_counts(query_db: dict) -> None:
    """Test per-file import counts (including external imports)."""
    graph = _graph(query_db)
    assert graph.file_import_counts["app/services/login.py"] == 3
    assert graph.file_import_counts["app/cli.py"] == 1
