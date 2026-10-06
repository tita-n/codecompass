"""Tree-sitter parser initialization and language-specific parsing logic."""

import hashlib
import importlib
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser, Tree

from codecompass.indexer.languages import LANGUAGES

# Map of CodeCompass language names to their tree-sitter Python binding modules.
LANGUAGE_MODULES: dict[str, str] = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "java": "tree_sitter_java",
    "go": "tree_sitter_go",
    "rust": "tree_sitter_rust",
    "php": "tree_sitter_php",
    "cpp": "tree_sitter_cpp",
    "c_sharp": "tree_sitter_c_sharp",
}

# Some bindings expose their language factory under a non-standard name.
LANGUAGE_FACTORY_NAMES: dict[str, list[str]] = {
    "typescript": ["language_typescript", "language_tsx"],
    "php": ["language_php", "language_php_only"],
}


def _build_language(lang_name: str, module: Any) -> Any:
    """Build a tree-sitter ``Language``, tolerating API differences across versions.

    Modern bindings expose a ``language()`` factory that returns a capsule to
    wrap with ``Language(...)``; older bindings returned a ``Language`` object
    directly.
    """
    factory = None
    for factory_name in LANGUAGE_FACTORY_NAMES.get(lang_name, ["language"]):
        if hasattr(module, factory_name):
            factory = getattr(module, factory_name)
            break
    if factory is None:
        raise RuntimeError(f"{module.__name__} does not expose a language factory")

    raw = factory()
    try:
        return Language(raw)
    except TypeError:
        # tree-sitter < 0.21 returned a Language object directly from the factory.
        return raw


def init_parsers() -> dict[str, Parser]:
    """Initialize Tree-sitter parsers for all supported languages.

    Languages whose grammar bindings are not installed are skipped with a
    warning so indexing can still proceed for the remaining languages.
    """
    parsers: dict[str, Parser] = {}
    for lang_name in LANGUAGES:
        module_name = LANGUAGE_MODULES.get(lang_name)
        if module_name is None:
            continue
        try:
            module = importlib.import_module(module_name)
            language = _build_language(lang_name, module)
            parser = Parser()
            parser.language = language
            parsers[lang_name] = parser
        except Exception as exc:
            print(f"Warning: Could not load parser for {lang_name}: {exc}")
    return parsers


def parse_file(
    file_path: Path,
    content: str,
    parser: Parser,
    language: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Parse a file and extract symbols, calls, and imports.

    Returns:
        Tuple of (symbols, calls, imports) where each is a list of dicts ready
        to be persisted by the indexer.

    Phase 1 implements full extraction for Python; other languages are tracked
    as files but their symbol extraction lands in Phase 2.
    """
    if language == "python":
        return parse_python_file(file_path, content, parser)
    return [], [], []


def parse_python_file(
    file_path: Path,
    content: str,
    parser: Parser,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Parse a Python file using Tree-sitter.

    Returns:
        (symbols, calls, imports)
    """
    source_bytes = content.encode("utf-8")
    tree = parser.parse(source_bytes)

    symbols = extract_symbols_from_python(tree, source_bytes)
    calls = extract_calls_from_python(tree, source_bytes)
    imports = extract_imports_from_python(tree, source_bytes)

    return symbols, calls, imports


# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------


def _first_child(node: Any, child_type: str) -> Any | None:
    """Return the first direct child of ``node`` with the given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _node_text(node: Any | None) -> str | None:
    """Decode a tree-sitter node's text to a string."""
    if node is None:
        return None
    return bytes(node.text).decode("utf-8", errors="replace")


def _strip_quotes(text: str) -> str:
    """Strip surrounding quote characters from a string literal."""
    for quote in ('"""', "'''"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 6:
            return text[3:-3]
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        return text[1:-1]
    return text


# ---------------------------------------------------------------------------
# Python symbol / call / import extraction
# ---------------------------------------------------------------------------

# Node types that add a decision path for cyclomatic complexity purposes.
# Each entry counts ONE independent path: ``if``/``elif`` branches, loops,
# ``except`` handlers (the ``try`` itself adds no path), boolean operators,
# ternary expressions and ``match`` cases (the ``match`` itself adds no
# path). Comprehension ``if`` clauses count via ``if_clause``; the
# comprehension's own ``for`` counts via ``for_in_clause``. The legacy
# ``comprehension_if_clause`` spelling is kept for older grammars.
_DECISION_NODE_TYPES = {
    "if_statement",
    "elif_clause",
    "for_statement",
    "for_in_clause",
    "while_statement",
    "except_clause",
    "conditional_expression",
    "boolean_operator",
    "case_clause",
    "if_clause",
    "comprehension_if_clause",
}


def _compute_complexity(node: Any) -> int:
    """Estimate cyclomatic complexity (1 + decision points) of a definition node.

    Nested function/class definitions are excluded; they are measured
    separately as their own symbols.
    """
    complexity = 1

    def visit(current: Any) -> None:
        nonlocal complexity
        if current.type in ("function_definition", "class_definition"):
            return
        if current.type in _DECISION_NODE_TYPES:
            complexity += 1
        for child in current.children:
            visit(child)

    for child in node.children:
        visit(child)
    return complexity


def extract_symbols_from_python(tree: Tree, source_bytes: bytes) -> list[dict]:
    """Extract symbols (functions, classes, methods) from a Python AST.

    Each symbol is a dict with keys: name, kind, signature, line_start,
    line_end, docstring and parent (the name of the enclosing symbol, if any).
    """
    symbols: list[dict] = []
    stack: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if node.type in ("function_definition", "class_definition"):
            name = _node_text(_first_child(node, "identifier"))
            if name is None:
                return
            is_class = node.type == "class_definition"
            if is_class:
                kind = "class"
            elif stack and stack[-1]["kind"] == "class":
                kind = "method"
            else:
                kind = "function"

            entry: dict[str, Any] = {
                "name": name,
                "kind": kind,
                "signature": _extract_signature(node, is_class),
                "line_start": node.start_point.row + 1,
                "line_end": node.end_point.row + 1,
                "docstring": _extract_docstring(node),
                "complexity": None if is_class else _compute_complexity(node),
                "parent": stack[-1]["name"] if stack else None,
            }
            symbols.append(entry)

            stack.append(entry)
            for child in node.children:
                visit(child)
            stack.pop()
            return

        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return symbols


def _extract_signature(node: Any, is_class: bool) -> str | None:
    """Extract a human-readable signature (parameters / superclasses) for a symbol."""
    if is_class:
        superclasses = _first_child(node, "superclasses")
        return _node_text(superclasses) if superclasses is not None else None
    parameters = _first_child(node, "parameters")
    return _node_text(parameters) if parameters is not None else None


def _extract_docstring(node: Any) -> str | None:
    """Extract the docstring (first string statement in the body) of a symbol."""
    block = _first_child(node, "block")
    if block is None:
        return None
    named = block.named_children
    if not named or named[0].type != "expression_statement":
        return None
    string_node = _first_child(named[0], "string")
    if string_node is None:
        return None
    text = _node_text(string_node)
    return _strip_quotes(text) if text is not None else None


def extract_calls_from_python(tree: Tree, source_bytes: bytes) -> list[dict]:
    """Extract call relationships from a Python AST.

    Calls are attributed to the innermost enclosing function or method. Module
    level calls (no enclosing symbol) are skipped because the schema requires a
    caller symbol. Each call dict has keys: caller, callee, line, call_text.
    """
    calls: list[dict] = []
    stack: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if node.type in ("function_definition", "class_definition"):
            if _push_symbol(node, stack):
                for child in node.children:
                    visit(child)
                stack.pop()
            return

        if node.type == "call":
            _record_call(node, stack, calls)

        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return calls


def _push_symbol(node: Any, stack: list[dict[str, Any]]) -> bool:
    """Push a function/class symbol onto the traversal stack."""
    name = _node_text(_first_child(node, "identifier"))
    if name is None:
        return False
    stack.append(
        {
            "kind": _symbol_kind(node, stack),
            "name": name,
            "line_start": node.start_point.row + 1,
            "line_end": node.end_point.row + 1,
        }
    )
    return True


def _symbol_kind(node: Any, stack: list[dict[str, Any]]) -> str:
    """Determine the kind (class/method/function) of a definition node."""
    if node.type == "class_definition":
        return "class"
    if stack and stack[-1]["kind"] == "class":
        return "method"
    return "function"


def _record_call(node: Any, stack: list[dict[str, Any]], calls: list[dict]) -> None:
    """Record a call node, attributing it to the innermost enclosing callable."""
    caller = _innermost_callable(stack)
    if caller is None:
        return
    callee = _extract_callee_name(node)
    if callee is None:
        return
    calls.append(
        {
            "caller": caller["name"],
            "callee": callee,
            "line": node.start_point.row + 1,
            "call_text": node.text.decode("utf-8", errors="replace"),
        }
    )


def _innermost_callable(stack: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the innermost function/method on the traversal stack, if any."""
    for entry in reversed(stack):
        if entry["kind"] in ("function", "method"):
            return entry
    return None


def _extract_callee_name(call_node: Any) -> str | None:
    """Extract the called name from a call node (e.g. ``foo`` in ``foo()``)."""
    named = call_node.named_children
    if not named:
        return None
    func = named[0]
    # Older grammars wrap the called expression in a "function" node.
    if func.type == "function" and func.named_children:
        func = func.named_children[0]
    if func.type == "identifier":
        return _node_text(func)
    if func.type == "attribute":
        # e.g. ``os.path.join(...)`` -> "os.path.join", ``self.add(...)`` -> "self.add".
        # Keeping the full dotted path lets the resolver disambiguate across files.
        return _node_text(func)
    # Fall back to any identifier (e.g. chained calls like ``foo()()``).
    return _node_text(_first_child(func, "identifier"))


def extract_imports_from_python(tree: Tree, source_bytes: bytes) -> list[dict]:
    """Extract import statements from a Python AST.

    Each import dict has keys: module, imported_name, alias, is_default,
    is_from_import and line.
    """
    imports: list[dict] = []

    def visit(node: Any) -> None:
        if node.type == "import_statement":
            imports.extend(_extract_import_statement(node))
        elif node.type == "import_from_statement":
            imports.extend(_extract_import_from_statement(node))
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return imports


def _extract_import_statement(node: Any) -> list[dict]:
    """Extract imports from an ``import a, b as c`` statement."""
    imports: list[dict] = []
    for child in node.named_children:
        if child.type == "dotted_name":
            imports.append(
                {
                    "module": _node_text(child),
                    "imported_name": None,
                    "alias": None,
                    "is_default": 0,
                    "is_from_import": 0,
                    "line": node.start_point.row + 1,
                }
            )
        elif child.type == "aliased_import":
            imports.append(_aliased_import(child, node, is_from_import=0))
    return imports


def _extract_import_from_statement(node: Any) -> list[dict]:
    """Extract imports from a ``from m import a as b`` statement."""
    imports: list[dict] = []
    module = _from_import_module(node)

    # The ``import`` keyword is an anonymous node, so scan all children.
    children = node.children
    import_idx = next((i for i, c in enumerate(children) if c.type == "import"), None)
    name_children = (
        [c for c in children[import_idx + 1 :] if c.is_named] if import_idx is not None else []
    )

    for child in name_children:
        if child.type == "dotted_name":
            imports.append(
                {
                    "module": module,
                    "imported_name": _node_text(child),
                    "alias": None,
                    "is_default": 0,
                    "is_from_import": 1,
                    "line": node.start_point.row + 1,
                }
            )
        elif child.type == "aliased_import":
            imports.append(_aliased_import(child, node, is_from_import=1, module=module))
        elif child.type == "wildcard_import":
            imports.append(
                {
                    "module": module,
                    "imported_name": "*",
                    "alias": None,
                    "is_default": 0,
                    "is_from_import": 1,
                    "line": node.start_point.row + 1,
                }
            )
    return imports


def _aliased_import(
    node: Any,
    parent: Any,
    is_from_import: int,
    module: str | None = None,
) -> dict:
    """Build an import dict for an ``aliased_import`` node (``x as y``)."""
    names = [c for c in node.named_children if c.type == "dotted_name"]
    alias_node = _first_child(node, "as")
    alias = _node_text(node.named_children[-1]) if alias_node is not None else None

    if is_from_import:
        imported_name = _node_text(names[0]) if names else None
        return {
            "module": module,
            "imported_name": imported_name,
            "alias": alias,
            "is_default": 0,
            "is_from_import": 1,
            "line": parent.start_point.row + 1,
        }

    module_name = _node_text(names[0]) if names else None
    return {
        "module": module_name,
        "imported_name": None,
        "alias": alias,
        "is_default": 0,
        "is_from_import": 0,
        "line": parent.start_point.row + 1,
    }


def _from_import_module(node: Any) -> str:
    """Extract the module name of a ``from ... import ...`` statement."""
    relative = _first_child(node, "relative_import")
    if relative is not None:
        return _node_text(relative) or "."
    dotted = _first_child(node, "dotted_name")
    if dotted is not None:
        return _node_text(dotted) or ""
    return ""


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
