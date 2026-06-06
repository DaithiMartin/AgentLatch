"""Smoke test: the package imports and exposes a version."""

import agentlatch


def test_package_imports_with_version() -> None:
    assert agentlatch.__version__
