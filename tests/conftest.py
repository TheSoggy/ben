"""pytest configuration for the Ben engine tests."""

from __future__ import annotations


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "unit: fast in-process unit test")
    config.addinivalue_line("markers", "integration: hits external services")
