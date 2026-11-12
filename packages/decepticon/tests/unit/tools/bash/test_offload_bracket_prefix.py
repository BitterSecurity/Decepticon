from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from decepticon_sandbox.kernel import BackgroundJobTracker

from decepticon.tools.bash.bash import (
    _STATUS_PREFIXES,
    INLINE_LIMIT,
    bash,
    set_sandbox,
)


def _fake_sandbox():
    sandbox = MagicMock()
    sandbox._jobs = BackgroundJobTracker()
    sandbox.execute = MagicMock()
    sandbox.upload_files = MagicMock()
    return sandbox


OVER_LIMIT = INLINE_LIMIT + 1


@pytest.mark.parametrize(
    "prefix",
    [
        "[INF] ",
        "[WRN] ",
        "[ERR] ",
        "[2024-01-01",
        "[nuclei-template] ",
        "[",
    ],
)
def test_bracket_prefixed_real_output_is_offloaded(prefix):
    sandbox = _fake_sandbox()
    large_output = prefix + "x" * OVER_LIMIT
    sandbox.execute_tmux_async = AsyncMock(return_value=large_output)
    set_sandbox(sandbox)

    result = asyncio.run(
        bash.ainvoke(
            {"command": "nuclei -u http://target", "description": "Run nuclei against the target"},
            config={"configurable": {"workspace_path": "/workspace"}},
        )
    )

    assert "not written" in result or "truncated" in result


@pytest.mark.parametrize("status_prefix", _STATUS_PREFIXES)
def test_status_prefixed_synthetic_output_is_not_offloaded(status_prefix):
    sandbox = _fake_sandbox()
    large_synthetic = status_prefix + "x" * OVER_LIMIT
    sandbox.execute_tmux_async = AsyncMock(return_value=large_synthetic)
    set_sandbox(sandbox)

    result = asyncio.run(
        bash.ainvoke(
            {"command": "", "description": "Read the current screen"},
            config={"configurable": {"workspace_path": "/workspace"}},
        )
    )

    assert result == large_synthetic


def test_non_bracket_large_output_is_offloaded():
    sandbox = _fake_sandbox()
    large_output = "plain " + "x" * OVER_LIMIT
    sandbox.execute_tmux_async = AsyncMock(return_value=large_output)
    set_sandbox(sandbox)

    result = asyncio.run(
        bash.ainvoke(
            {"command": "cat big.txt", "description": "Print a large file"},
            config={"configurable": {"workspace_path": "/workspace"}},
        )
    )

    assert "not written" in result or "truncated" in result
