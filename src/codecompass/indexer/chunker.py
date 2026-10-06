"""Code chunking for embedding and retrieval.

Splits source files into small, overlapping, contiguous chunks that are suitable
for embedding and later retrieval. Chunks are generated per top-level symbol
(function/class) so each chunk keeps its surrounding context, plus separate
segments for top-level code that lives outside any symbol (imports, module
constants, docstrings). Every chunk is attributed to the innermost symbol whose
line range covers it, so method-level chunks carry the method name rather than
the enclosing class name.
"""

from typing import Any

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 200


def chunk_code(
    content: str,
    file_path: str,
    language: str,
    symbols: list[dict],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    """
    Split code into overlapping chunks.

    Args:
        content: Full source code.
        file_path: Repo-relative path of the file.
        language: Programming language name (stored as chunk metadata).
        symbols: List of symbol dicts (each with ``name``, ``kind``,
            ``line_start``, ``line_end`` and optionally ``parent``).
        chunk_size: Maximum chunk size in characters (default 500).
        overlap: Approximate overlap in characters between consecutive chunks
            within the same segment (default 200).

    Returns:
        List of chunk dicts with keys ``content``, ``file_path``,
        ``symbol_name``, ``line_start``, ``line_end``, ``chunk_index`` and
        ``language``. Chunks are ordered by position and ``chunk_index`` is a
        contiguous 0-based index within the file.
    """
    lines = content.splitlines()
    if not lines:
        return []

    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE
    if overlap < 0 or overlap >= chunk_size:
        overlap = max(chunk_size // 2, 0)

    segments = _build_segments(lines, symbols)

    chunks: list[dict] = []
    for start, end in segments:
        segment_lines = lines[start:end]
        # Skip whitespace-only segments (e.g. blank lines between functions):
        # they add noise to both keyword and vector search.
        if not any(line.strip() for line in segment_lines):
            continue
        for sub_start, sub_end in _sliding_ranges(segment_lines, chunk_size, overlap):
            line_start = start + sub_start + 1  # 1-based, inclusive
            line_end = start + sub_end  # sub_end is exclusive -> 1-based inclusive
            chunks.append(
                {
                    "content": "\n".join(segment_lines[sub_start:sub_end]),
                    "file_path": file_path,
                    "symbol_name": _attribute_symbol(symbols, line_start, line_end),
                    "line_start": line_start,
                    "line_end": line_end,
                    "chunk_index": 0,  # assigned below, after sorting
                    "language": language,
                }
            )

    chunks.sort(key=lambda c: (c["line_start"], c["line_end"]))
    for index, chunk in enumerate(chunks):
        chunk["chunk_index"] = index
    return chunks


def _symbol_range(symbol: dict) -> tuple[int, int] | None:
    """Return the 1-based inclusive (start, end) line range, or None if invalid."""
    try:
        start = int(symbol["line_start"])
        end = int(symbol["line_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if start < 1 or end < start:
        return None
    return start, end


def _is_covered_by(symbol_range: tuple[int, int], other: tuple[int, int]) -> bool:
    """True if ``symbol_range`` lies within ``other`` (equal or strictly smaller)."""
    return other[0] <= symbol_range[0] and symbol_range[1] <= other[1]


def _top_level_ranges(symbols: list[dict], total: int) -> list[tuple[int, int]]:
    """Return disjoint 0-based ranges of the symbols not nested in another.

    A symbol is top-level when it isn't covered by another symbol's range
    (handles both explicit ``parent`` links and raw containment). Partially
    overlapping (malformed) ranges are clipped to stay disjoint.
    """
    raw: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for symbol in symbols:
        span = _symbol_range(symbol)
        if span is None:
            continue
        start = max(span[0] - 1, 0)
        end = min(span[1], total)
        if start >= end or (start, end) in seen:
            continue
        seen.add((start, end))
        raw.append((start, end))

    sorted_ranges = sorted(raw, key=lambda r: (r[0], -(r[1] - r[0])))
    top_level: list[tuple[int, int]] = []
    for span in sorted_ranges:
        if any(_is_covered_by(span, kept) for kept in top_level):
            continue
        if top_level and span[0] < top_level[-1][1]:
            span = (top_level[-1][1], span[1])
            if span[0] >= span[1]:
                continue
        top_level.append(span)
    return top_level


def _gap_segments(covered: list[bool]) -> list[tuple[int, int]]:
    """Return runs of lines not covered by any symbol (top-level code)."""
    segments: list[tuple[int, int]] = []
    i = 0
    while i < len(covered):
        if covered[i]:
            i += 1
            continue
        j = i
        while j < len(covered) and not covered[j]:
            j += 1
        segments.append((i, j))
        i = j
    return segments


def _build_segments(lines: list[str], symbols: list[dict]) -> list[tuple[int, int]]:
    """
    Compute the disjoint line segments to chunk.

    Top-level symbols (those not nested inside another symbol, determined via
    the ``parent`` key or by line-range containment) each own a segment. Any
    line not covered by a symbol forms a separate top-level gap segment, so the
    whole file is covered exactly once with no duplicated content.
    """
    total = len(lines)
    top_level = _top_level_ranges(symbols, total)

    covered = [False] * total
    for start, end in top_level:
        for i in range(start, end):
            covered[i] = True

    segments = top_level + _gap_segments(covered)
    segments.sort()
    return segments


def _sliding_ranges(lines: list[str], size: int, overlap: int) -> list[tuple[int, int]]:
    """
    Slide a character-budget window over ``lines``.

    Returns half-open index ranges. Consecutive ranges within a segment share
    approximately ``overlap`` characters of context. A single line longer than
    ``size`` is emitted alone so progress is always guaranteed.
    """
    ranges: list[tuple[int, int]] = []
    total = len(lines)
    i = 0
    while i < total:
        budget = 0
        j = i
        while j < total:
            line_length = len(lines[j]) + 1  # +1 for the joining newline
            if budget + line_length > size and j > i:
                break
            budget += line_length
            j += 1
        ranges.append((i, j))
        if j >= total:
            break

        # Walk backwards so the next chunk re-includes ~`overlap` shared chars.
        k = j
        shared = 0
        while k > i and shared < overlap:
            k -= 1
            shared += len(lines[k]) + 1
        i = max(k, i + 1)  # always make progress
    return ranges


def _attribute_symbol(symbols: list[dict], line_start: int, line_end: int) -> str | None:
    """Pick the innermost symbol overlapping the given 1-based inclusive range.

    The symbol covering the most chunk lines wins; ties go to the smallest
    span, which selects the nested (innermost) symbol for chunks that sit
    entirely inside a method, while a chunk that also spans the class header
    keeps the class name.
    """
    best: dict[str, Any] | None = None
    best_score = (0, 0)
    for symbol in symbols:
        span = _symbol_range(symbol)
        if span is None:
            continue
        overlap_lines = min(span[1], line_end) - max(span[0], line_start) + 1
        if overlap_lines <= 0:
            continue
        span_size = span[1] - span[0] + 1
        score = (overlap_lines, -span_size)
        if score > best_score:
            best = symbol
            best_score = score
    if best is None:
        return None
    name = best.get("name")
    return str(name) if name is not None else None
