"""Module A: imports B (circular pair with b.py)."""

from analysis_repo.b import helper_b

VALUE_A = 1


def helper_a(x: int) -> int:
    """Add one via the sibling module."""
    return helper_b(x) + VALUE_A


def run_a(x: int) -> int:
    """Entry point used by coupled.py."""
    if x > 0:
        return helper_a(x)
    return 0
