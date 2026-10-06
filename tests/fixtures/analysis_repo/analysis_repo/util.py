"""Shared utilities: imported by many modules (fan-in hub)."""


def clamp(value: int, low: int = 0, high: int = 100) -> int:
    """Clamp value into [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def is_valid(token: str) -> bool:
    """Check a token is non-empty."""
    return bool(token and token.strip())
