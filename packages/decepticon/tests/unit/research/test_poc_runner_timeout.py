"""Tests for sandbox_runner timeout + typed-error classification."""

from __future__ import annotations

import asyncio

import pytest

from decepticon.tools.research import poc as poc_mod
from decepticon.tools.research.poc import (
    POC_ERR_SANDBOX,
    POC_ERR_TIMEOUT,
    sandbox_runner,
)


class _HangingSandbox:
    async def execute_tmux_async(
        self, command: str, session: str, timeout: int, is_input: bool
    ) -> str:
        await asyncio.sleep(5.0)  # exceeds patched runner timeout
        return "should not reach"


class _RaisingSandbox:
    async def execute_tmux_async(
        self, command: str, session: str, timeout: int, is_input: bool
    ) -> str:
        raise RuntimeError("boom")


class _OkSandbox:
    async def execute_tmux_async(
        self, command: str, session: str, timeout: int, is_input: bool
    ) -> str:
        return "ok\n[Exit code: 0]\n[DCPTN_COMPLETE exit=0]"


@pytest.mark.asyncio
async def test_runner_times_out_with_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(poc_mod, "POC_RUNNER_TIMEOUT_SECONDS", 0.05)
    runner = sandbox_runner(_HangingSandbox())
    out, err, code = await runner("noop")
    assert out == ""
    assert err.startswith(POC_ERR_TIMEOUT)
    assert code == -1


@pytest.mark.asyncio
async def test_runner_crash_classified_as_sandbox_error() -> None:
    runner = sandbox_runner(_RaisingSandbox())
    out, err, code = await runner("noop")
    assert out == ""
    assert err.startswith(POC_ERR_SANDBOX)
    assert "RuntimeError" in err
    assert code == -1


@pytest.mark.asyncio
async def test_runner_happy_path_preserves_shape() -> None:
    runner = sandbox_runner(_OkSandbox())
    out, err, code = await runner("id")
    assert "ok" in out
    assert err == ""
    assert code == 0


@pytest.mark.asyncio
async def test_runner_timeout_override_kwarg() -> None:
    runner = sandbox_runner(_HangingSandbox(), timeout=0.05)
    out, err, code = await runner("noop")
    assert err.startswith(POC_ERR_TIMEOUT)
    assert code == -1
