"""Shared pytest configuration for all test suites."""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async (requires pytest-asyncio)"
    )
