"""Fan-out hub: imports many sibling modules."""

from analysis_repo import a, b
from analysis_repo import complex as complex_mod
from analysis_repo import leaf1, leaf2, leaf3, leaf4, util


def orchestrate(x: int) -> int:
    """Touch every import so the fan-out is real."""
    total = a.run_a(x)
    total += b.helper_b(x)
    total += util.clamp(x)
    total += leaf1.one(x)
    total += leaf2.two(x)
    total += leaf3.three(x)
    total += leaf4.four(x)
    if complex_mod.greet("x"):
        total += 1
    return total
