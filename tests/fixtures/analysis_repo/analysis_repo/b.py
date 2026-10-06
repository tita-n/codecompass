"""Module B: imports A (circular pair with a.py)."""

from analysis_repo.a import VALUE_A


def helper_b(x: int) -> int:
    """Double the input, offset by A's constant."""
    if x < 0:
        return -x + VALUE_A
    return x * 2
