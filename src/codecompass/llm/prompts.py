"""Prompt templates and context assembly for the LLM orchestration layer.

Prompts are provider-agnostic plain text. The QA prompt is written to make
citations verifiable: answers must reference the exact ``### File: path (lines
a-b)`` headers supplied in the context, and must refuse to answer when the
retrieved chunks do not contain the information.
"""

import logging
import math
from typing import Any

logger = logging.getLogger("codecompass.llm")

#: Approximate token budget for retrieved chunks (~4 chars per token).
DEFAULT_CONTEXT_TOKEN_BUDGET = 8_000

QA_SYSTEM_PROMPT = """\
You are CodeCompass, a code intelligence assistant. You answer questions about \
a codebase using ONLY the retrieved code chunks supplied with the question.

Rules:
1. Use ONLY the provided context. Never invent code, files, symbols or line \
numbers that are not in the context.
2. Cite every claim inline using the exact source header format, e.g. \
[app/auth.py:4-18]. If several chunks support a point, cite them all.
3. If the context does not contain the answer, say exactly: \
"I don't have enough information in the retrieved code to answer that." \
Then suggest what symbol, file or keyword would help.
4. Prefer concrete, technical language; quote short identifiers and snippets \
rather than paraphrasing vaguely.
5. Answer in Markdown. Use short paragraphs and bullet lists. Use a fenced \
code block only when quoting code verbatim.
6. Be concise: lead with the direct answer, then supporting detail.
"""

EXPLAIN_SYSTEM_PROMPT = """\
You are CodeCompass, a code intelligence assistant explaining a specific code \
block from a codebase.

Rules:
1. Explain what the block does, step by step, in plain technical language.
2. Explain WHY it exists: what problem it solves, how it fits into the \
surrounding system, and what depends on it, when the context suggests it.
3. Call out edge cases, error handling, assumptions, and anything that looks \
fragile or surprising.
4. Do NOT rewrite or optimise the code; explanation only. If a fix seems \
worthwhile, mention it briefly at the end under "Notes".
5. Reference identifiers you mention with the supplied file and line range.
6. Answer in Markdown; keep it under ~300 words unless asked for more.
"""

SIMPLIFY_SYSTEM_PROMPT = """\
You are CodeCompass, a code intelligence assistant proposing a simplification \
of a specific code block.

Rules:
1. This is a READ-ONLY preview: you are not applying changes. Present the \
proposed replacement as a single fenced code block, clearly labelled as a \
preview.
2. Preserve the block's external behaviour, signature and public interface \
unless the user explicitly asked otherwise.
3. Explain what you changed and why, bullet by bullet, and note anything you \
deliberately left alone.
4. Call out trade-offs (readability, performance, risk) honestly.
5. Do NOT claim any file was modified. Answer in Markdown.
"""

_LANGUAGE_FALLBACK = "text"


def estimate_tokens(text: str) -> int:
    """Roughly estimate the token count of ``text`` (~4 characters/token)."""
    return math.ceil(len(text) / 4)


def _fence_language(chunk: dict[str, Any]) -> str:
    language = str(chunk.get("language") or "").strip()
    return language or _LANGUAGE_FALLBACK


def format_chunk(chunk: dict[str, Any]) -> str:
    """Format a single retrieved chunk as a markdown section with a header."""
    file_path = chunk.get("file_path", "<unknown>")
    line_start = chunk.get("line_start", 0)
    line_end = chunk.get("line_end", 0)
    header = f"### File: {file_path} (lines {line_start}-{line_end})"
    symbol_name = chunk.get("symbol_name")
    if symbol_name:
        header += f" — symbol: {symbol_name}"
    content = str(chunk.get("document") or chunk.get("content") or "")
    return f"{header}\n\n```{_fence_language(chunk)}\n{content}\n```"


def assemble_context(
    chunks: list[dict[str, Any]],
    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> str:
    """Format retrieved chunks into a single context block.

    Chunks are included in order until ``token_budget`` is reached; anything
    dropped is reported through the logger so oversized retrievals never
    silently lose code.
    """
    if not chunks:
        return ""

    sections: list[str] = []
    used = 0
    for chunk in chunks:
        section = format_chunk(chunk)
        cost = estimate_tokens(section)
        if used + cost > token_budget:
            if not sections:
                # Always include something: hard-truncate the first chunk.
                limit = max(token_budget, 1) * 4
                sections.append(section[:limit] + "\n...[context truncated]")
            break
        sections.append(section)
        used += cost

    if len(sections) < len(chunks):
        logger.warning(
            "Context truncated: using %d of %d chunks (~%d tokens, budget %d)",
            len(sections),
            len(chunks),
            used,
            token_budget,
        )
    return "\n\n".join(sections)


def assemble_user_prompt(question: str, context: str) -> str:
    """Build the user message for a Q&A turn (question + retrieved context)."""
    return f"Question: {question}\n\nContext:\n{context}"


def assemble_code_prompt(
    instruction: str,
    file_path: str,
    line_start: int,
    line_end: int,
    code: str,
    language: str = _LANGUAGE_FALLBACK,
) -> str:
    """Build a user message that presents a specific code block to review."""
    return (
        f"{instruction}\n\n"
        f"File: {file_path} (lines {line_start}-{line_end})\n\n"
        f"```{language}\n{code}\n```"
    )


def assemble_explain_prompt(
    file_path: str,
    line_start: int,
    line_end: int,
    code: str,
    language: str = _LANGUAGE_FALLBACK,
) -> str:
    """User message for the ``explain`` command."""
    return assemble_code_prompt(
        "Explain the following code block.",
        file_path,
        line_start,
        line_end,
        code,
        language,
    )


def assemble_simplify_prompt(
    file_path: str,
    line_start: int,
    line_end: int,
    code: str,
    language: str = _LANGUAGE_FALLBACK,
) -> str:
    """User message for the ``simplify`` command."""
    return assemble_code_prompt(
        "Suggest a simpler, cleaner version of the following code block.",
        file_path,
        line_start,
        line_end,
        code,
        language,
    )
