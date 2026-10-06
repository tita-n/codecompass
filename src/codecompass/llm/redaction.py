"""Best-effort redaction of secrets before sending code to cloud providers.

Local providers (Ollama) never need this; anything flagged as ``is_cloud``
gets its prompts passed through :func:`redact_secrets` first. The patterns
cover common credential shapes found in source code and config files - it is
a safety net, not a guarantee, but it prevents the obvious leaks.
"""

import re

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Private key blocks (PEM)
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    ),
    # Provider-issued API keys: sk-..., anthropic keys, OpenAI project keys
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    # AWS access key ids
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    # Slack / Stripe style tokens
    re.compile(r"\b(?:xox[baprs]-|sk_live_)[A-Za-z0-9\-]{10,}"),
    # key = value / key: value assignments (quoted or bare)
    re.compile(
        r"(?i)\b((?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|password|passwd|pwd))\b(\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})\3"
    ),
)


def redact_secrets(text: str) -> str:
    """Return ``text`` with apparent secrets replaced by ``[REDACTED]``.

    Args:
        text: Arbitrary text (prompt, code, config snippet).

    Returns:
        The text with credential-looking substrings masked.
    """
    if not text:
        return text

    redacted = _SECRET_PATTERNS[0].sub(REDACTED, text)
    for pattern in _SECRET_PATTERNS[1:-1]:
        redacted = pattern.sub(REDACTED, redacted)

    # Preserve the "name:" prefix so the LLM still sees the surrounding shape.
    assignment = _SECRET_PATTERNS[-1]
    redacted = assignment.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(3)}",
        redacted,
    )
    return redacted


def contains_secret(text: str) -> bool:
    """True if :func:`redact_secrets` would change ``text``."""
    return redact_secrets(text) != text
