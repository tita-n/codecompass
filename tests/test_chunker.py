"""Tests for the code chunker."""

from codecompass.indexer.chunker import chunk_code

MODULE_PY = '''\
"""Payments module."""

import os

def validate_payment(amount: float) -> bool:
    """Validate a payment amount before charging."""
    if amount <= 0:
        return False
    return amount < 10000
'''

VALIDATE_SYMBOL = {
    "name": "validate_payment",
    "kind": "function",
    "line_start": 5,
    "line_end": 9,
    "parent": None,
}


def test_empty_content_returns_no_chunks() -> None:
    """Test that empty files produce no chunks."""
    assert chunk_code("", "empty.py", "python", []) == []


def test_small_file_single_chunk() -> None:
    """Test a small file without symbols becomes one chunk covering all lines."""
    content = "import os\nimport sys\n"
    chunks = chunk_code(content, "m.py", "python", [])

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["content"] == "import os\nimport sys"
    assert chunk["file_path"] == "m.py"
    assert chunk["language"] == "python"
    assert chunk["symbol_name"] is None
    assert chunk["line_start"] == 1
    assert chunk["line_end"] == 2
    assert chunk["chunk_index"] == 0


def test_symbol_association_and_top_level_segment() -> None:
    """Test chunks are attributed to their symbol; top-level code stays free."""
    chunks = chunk_code(MODULE_PY, "payments.py", "python", [VALIDATE_SYMBOL])

    symbol_chunk = next(c for c in chunks if "def validate_payment" in c["content"])
    top_level_chunk = next(c for c in chunks if "import os" in c["content"])

    assert symbol_chunk["symbol_name"] == "validate_payment"
    assert symbol_chunk["line_start"] == 5
    assert symbol_chunk["line_end"] == 9
    assert top_level_chunk["symbol_name"] is None


def test_full_line_coverage_in_order() -> None:
    """Test chunks cover every line of the file and are indexed sequentially."""
    lines = [f"value_{i} = compute({i})  # step {i}" for i in range(80)]
    content = "\n".join(lines) + "\n"
    chunks = chunk_code(content, "m.py", "python", [])

    covered: set[int] = set()
    for chunk in chunks:
        covered.update(range(chunk["line_start"], chunk["line_end"] + 1))
    assert covered == set(range(1, len(lines) + 1))

    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    starts = [c["line_start"] for c in chunks]
    assert starts == sorted(starts)
    assert chunks[0]["line_start"] == 1
    assert chunks[-1]["line_end"] == len(lines)


def test_chunks_are_sized_and_overlapping() -> None:
    """Test the 500-char budget and ~200-char overlap between chunks."""
    lines = [f"value_{i} = compute({i})  # step {i}" for i in range(80)]
    content = "\n".join(lines) + "\n"
    chunks = chunk_code(content, "m.py", "python", [])

    assert len(chunks) >= 4
    for chunk in chunks:
        assert len(chunk["content"]) <= 500

    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert following["line_start"] > previous["line_start"]  # progress
        assert following["line_start"] <= previous["line_end"]  # overlap
        shared = sum(
            len(lines[i]) + 1 for i in range(following["line_start"] - 1, previous["line_end"])
        )
        assert shared >= 200


def test_custom_chunk_size_and_overlap() -> None:
    """Test that custom size/overlap parameters are honoured."""
    lines = [f"row_{i} = {i}" for i in range(60)]
    content = "\n".join(lines) + "\n"
    chunks = chunk_code(content, "m.py", "python", [], chunk_size=100, overlap=30)

    assert len(chunks) > 3
    for chunk in chunks:
        assert len(chunk["content"]) <= 100


def test_long_single_line_is_kept_intact() -> None:
    """Test a line longer than chunk_size is emitted whole (no lost content)."""
    line = 'payload = "' + ("a" * 2000) + '"'
    chunks = chunk_code(line + "\n", "m.py", "python", [])

    assert len(chunks) == 1
    assert chunks[0]["content"] == line


def test_innermost_symbol_wins_for_method_chunk() -> None:
    """Test a chunk inside a method is attributed to the method, not its class."""
    method_body = [f"        x{i} = {i} * {i}" for i in range(60)]
    lines = ["class Calculator:", '    """Calculator."""', "    def calculate(self):"]
    lines += method_body + ["        return x0"]
    content = "\n".join(lines) + "\n"
    total = len(lines)

    symbols = [
        {
            "name": "Calculator",
            "kind": "class",
            "line_start": 1,
            "line_end": total,
            "parent": None,
        },
        {
            "name": "calculate",
            "kind": "method",
            "line_start": 3,
            "line_end": total,
            "parent": "Calculator",
        },
    ]
    chunks = chunk_code(content, "calc.py", "python", symbols)

    assert len(chunks) > 1
    # First chunk spans the class header -> class name.
    assert chunks[0]["symbol_name"] == "Calculator"
    # Later chunks sit entirely within the method -> method name.
    assert chunks[-1]["line_start"] >= 4
    assert chunks[-1]["symbol_name"] == "calculate"


def test_nested_symbols_without_parent_key_dedupe() -> None:
    """Test containment alone prevents duplicate segments (no parent key)."""
    method_body = [f"        y{i} = {i}" for i in range(60)]
    lines = ["class Widget:", "    def render(self):"] + method_body + ["        return True"]
    content = "\n".join(lines) + "\n"
    total = len(lines)

    with_parent = [
        {"name": "Widget", "kind": "class", "line_start": 1, "line_end": total, "parent": None},
        {
            "name": "render",
            "kind": "method",
            "line_start": 2,
            "line_end": total,
            "parent": "Widget",
        },
    ]
    without_parent = [
        {"name": "Widget", "kind": "class", "line_start": 1, "line_end": total},
        {"name": "render", "kind": "method", "line_start": 2, "line_end": total},
    ]

    a = chunk_code(content, "w.py", "python", with_parent)
    b = chunk_code(content, "w.py", "python", without_parent)

    assert [(c["line_start"], c["line_end"]) for c in a] == [
        (c["line_start"], c["line_end"]) for c in b
    ]
    # No duplicated coverage: union equals the whole file.
    covered: set[int] = set()
    for chunk in b:
        covered.update(range(chunk["line_start"], chunk["line_end"] + 1))
    assert covered == set(range(1, total + 1))


def test_invalid_symbol_ranges_are_ignored() -> None:
    """Test malformed symbol dicts don't break chunking."""
    content = "a = 1\nb = 2\n"
    symbols: list[dict] = [
        {"name": "bad", "kind": "function", "line_start": 0, "line_end": 0},
        {"name": "worse", "kind": "function", "line_start": 99, "line_end": 5},
        {"name": "empty"},
    ]
    chunks = chunk_code(content, "m.py", "python", symbols)

    assert len(chunks) == 1
    assert chunks[0]["symbol_name"] is None
    assert chunks[0]["line_end"] == 2


def test_blank_lines_between_symbols_are_skipped() -> None:
    """Test whitespace-only segments don't become chunks."""
    content = (
        "def first():\n" "    return 1\n" "\n" "\n" "\n" "\n" "def second():\n" "    return 2\n"
    )
    symbols = [
        {"name": "first", "kind": "function", "line_start": 1, "line_end": 2, "parent": None},
        {"name": "second", "kind": "function", "line_start": 7, "line_end": 8, "parent": None},
    ]
    chunks = chunk_code(content, "m.py", "python", symbols)

    assert len(chunks) == 2
    assert all(c["content"].strip() for c in chunks)
    assert {c["symbol_name"] for c in chunks} == {"first", "second"}
