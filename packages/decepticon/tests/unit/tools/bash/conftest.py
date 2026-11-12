"""Shared fixtures for bash tool tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _docker_sandbox_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the plain daemon (docker provider) transport."""
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "docker")
