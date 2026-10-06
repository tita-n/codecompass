"""Sample Python code for use in tests."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def calculate(a: int, b: int, operation: str) -> int:
    """Calculate based on operation."""
    if operation == "add":
        return add(a, b)
    elif operation == "multiply":
        return multiply(a, b)
    else:
        raise ValueError(f"Unknown operation: {operation}")


class Calculator:
    """A simple calculator."""

    def __init__(self, name: str = "Calculator"):
        self.name = name

    def add(self, a: int, b: int) -> int:
        return a + b

    def multiply(self, a: int, b: int) -> int:
        return a * b
