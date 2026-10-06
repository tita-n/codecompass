"""Dummy test to ensure pytest is working."""

import pytest


def test_dummy() -> None:
    """Always passes."""
    assert True


@pytest.mark.unit
def test_version_import() -> None:
    """Test that version is importable."""
    from codecompass import __version__

    assert __version__ == "0.0.1"
